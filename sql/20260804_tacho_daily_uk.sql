-- Dashboard-only local-time projection for tachograph records.
--
-- `tacho_daily` remains the immutable UTC source: tachograph card timestamps
-- are stored as a UTC date plus UTC `time without time zone` values.  This
-- view derives UK civil time (GMT/BST) at query time, so it follows daylight
-- saving changes without rewriting historic compliance records.

CREATE OR REPLACE VIEW public.tacho_daily_uk AS
SELECT
  t.*,
  'Europe/London'::text AS display_timezone,
  timezone(
    'Europe/London',
    (t.trip_date + t.card_in_time) AT TIME ZONE 'UTC'
  )::date AS trip_date_uk,
  timezone(
    'Europe/London',
    (t.trip_date + t.card_in_time) AT TIME ZONE 'UTC'
  )::time AS card_in_time_uk,
  timezone(
    'Europe/London',
    (
      t.trip_date + t.card_out_time +
      CASE
        WHEN t.card_in_time IS NOT NULL
         AND t.card_out_time < t.card_in_time THEN interval '1 day'
        ELSE interval '0 days'
      END
    ) AT TIME ZONE 'UTC'
  )::time AS card_out_time_uk
FROM public.tacho_daily AS t;

COMMENT ON VIEW public.tacho_daily_uk IS
  'UK-local (GMT/BST) dashboard projection of tacho_daily. Original UTC columns remain unchanged.';

-- The reader service account can query the view for its own status/dashboard use.
GRANT SELECT ON public.tacho_daily_uk TO tacho_writer;
