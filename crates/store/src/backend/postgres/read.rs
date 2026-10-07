/*
 * SPDX-FileCopyrightText: 2020 Stalwart Labs LLC <hello@stalw.art>
 *
 * SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-SEL
 */

use super::{PostgresStore, into_error, is_timeout_error};
use crate::{
    Deserialize, IterateParams, Key, ValueKey, backend::postgres::into_pool_error,
    write::ValueClass,
};
use futures::{TryStreamExt, pin_mut};

impl PostgresStore {
    pub(crate) async fn get_value<U>(&self, key: impl Key) -> trc::Result<Option<U>>
    where
        U: Deserialize + 'static,
    {
        let conn = self.conn_pool.get().await.map_err(into_pool_error)?;
        let s = conn
            .prepare_cached(&format!(
                "SELECT v FROM {} WHERE k = $1",
                char::from(key.subspace())
            ))
            .await
            .map_err(into_error)?;
        let key = key.serialize(0);
        conn.query_opt(&s, &[&key])
            .await
            .map_err(into_error)
            .and_then(|r| {
                if let Some(r) = r {
                    Ok(Some(U::deserialize_with_key(&key, r.get(0))?))
                } else {
                    Ok(None)
                }
            })
    }

    pub(crate) async fn key_exists(&self, key: impl Key) -> trc::Result<bool> {
        let conn = self.conn_pool.get().await.map_err(into_pool_error)?;
        let s = conn
            .prepare_cached(&format!(
                "SELECT 1 FROM {} WHERE k = $1",
                char::from(key.subspace())
            ))
            .await
            .map_err(into_error)?;
        let key = key.serialize(0);
        conn.query_opt(&s, &[&key])
            .await
            .map_err(into_error)
            .map(|r| r.is_some())
    }

    pub(crate) async fn iterate<T: Key>(
        &self,
        params: IterateParams<T>,
        mut cb: impl for<'x> FnMut(&'x [u8], &'x [u8]) -> trc::Result<bool> + Sync + Send,
    ) -> trc::Result<()> {
        const INITIAL_PAGE_SIZE: usize = 1000;
        const MIN_PAGE_SIZE: usize = 10;

        let conn = self.conn_pool.get().await.map_err(into_pool_error)?;
        let table = char::from(params.begin.subspace());
        let keys = if params.values { "k, v" } else { "k" };
        let direction = if params.ascending { "ASC" } else { "DESC" };
        let query = format!(
            "SELECT {keys} FROM {table} WHERE k >= $1 AND k <= $2 ORDER BY k {direction}"
        );
        let mut from = params.begin.serialize(0);
        let mut to = params.end.serialize(0);
        let mut resume_key: Option<Vec<u8>> = None;
        // Keep the single streaming query when it succeeds. After a timeout,
        // bound each statement and reduce its page size if it cannot progress.
        let mut page_size: Option<usize> = None;

        loop {
            let limit = if params.first {
                " LIMIT 1".to_string()
            } else if let Some(page_size) = page_size {
                format!(" LIMIT {page_size}")
            } else {
                String::new()
            };
            let s = conn
                .prepare_cached(&format!("{query}{limit}"))
                .await
                .map_err(into_error)?;
            let mut last_key = None;
            let mut row_count = 0;
            let mut timed_out = false;

            {
                let rows = match conn.query_raw(&s, &[&from, &to]).await {
                    Ok(rows) => rows,
                    Err(err)
                        if !params.first
                            && is_timeout_error(&err)
                            && page_size.is_none_or(|size| size > MIN_PAGE_SIZE) =>
                    {
                        page_size = Some(
                            page_size
                                .map(|size| (size / 2).max(MIN_PAGE_SIZE))
                                .unwrap_or(INITIAL_PAGE_SIZE),
                        );
                        continue;
                    }
                    Err(err) => return Err(into_error(err)),
                };

                pin_mut!(rows);

                loop {
                    match rows.try_next().await {
                        Ok(Some(row)) => {
                            row_count += 1;
                            let key = row.try_get::<_, &[u8]>(0).map_err(into_error)?;
                            let value = if params.values {
                                row.try_get::<_, &[u8]>(1).map_err(into_error)?
                            } else {
                                b"".as_slice()
                            };

                            // Preserve this boundary across retries that time out
                            // after returning only the already-delivered row.
                            if resume_key.as_deref() == Some(key) {
                                continue;
                            }

                            if !cb(key, value)? {
                                return Ok(());
                            }

                            last_key = Some(key.to_vec());
                        }
                        Ok(None) => break,
                        Err(err) => {
                            if params.first
                                || !is_timeout_error(&err)
                                || (last_key.is_none()
                                    && page_size.is_some_and(|size| size <= MIN_PAGE_SIZE))
                            {
                                return Err(into_error(err));
                            }
                            timed_out = true;
                            break;
                        }
                    }
                }
            }

            let full_page = page_size.is_some_and(|size| row_count == size);
            if let Some(last_key) = last_key {
                if timed_out || full_page {
                    if params.ascending {
                        from.clone_from(&last_key);
                    } else {
                        to.clone_from(&last_key);
                    }
                    resume_key = Some(last_key);
                } else {
                    return Ok(());
                }
            } else if !timed_out {
                return Ok(());
            }

            if timed_out {
                page_size = Some(
                    page_size
                        .map(|size| (size / 2).max(MIN_PAGE_SIZE))
                        .unwrap_or(INITIAL_PAGE_SIZE),
                );
            }
        }
    }

    pub(crate) async fn get_counter(
        &self,
        key: impl Into<ValueKey<ValueClass>> + Sync + Send,
    ) -> trc::Result<i64> {
        let key = key.into();
        let table = char::from(key.subspace());
        let key = key.serialize(0);

        let conn = self.conn_pool.get().await.map_err(into_pool_error)?;
        let s = conn
            .prepare_cached(&format!("SELECT v FROM {table} WHERE k = $1"))
            .await
            .map_err(into_error)?;
        match conn.query_opt(&s, &[&key]).await {
            Ok(Some(row)) => row.try_get(0).map_err(into_error),
            Ok(None) => Ok(0),
            Err(e) => Err(into_error(e)),
        }
    }
}
