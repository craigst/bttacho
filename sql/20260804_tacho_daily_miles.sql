-- Dashboard-only miles and odometer-gap projection.
-- The source table remains in tachograph-native kilometres and UTC values.

CREATE OR REPLACE VIEW public.tacho_daily_miles AS
WITH ordered AS (
  SELECT
    t.*,
    lag(t.end_mileage) OVER (
      PARTITION BY t.vehicle_registration
      ORDER BY t.trip_date, t.card_in_time, t.trip_uid
    ) AS previous_end_mileage
  FROM public.tacho_daily AS t
)
SELECT
  ordered.*,
  round(ordered.start_mileage * 0.621371)::integer AS start_miles,
  round(ordered.end_mileage * 0.621371)::integer AS end_miles,
  round(ordered.distance_km * 0.621371)::integer AS distance_miles,
  CASE
    WHEN ordered.previous_end_mileage IS NULL THEN NULL
    ELSE greatest(0, ordered.start_mileage - ordered.previous_end_mileage)
  END AS unaccounted_km,
  CASE
    WHEN ordered.previous_end_mileage IS NULL THEN NULL
    ELSE round(greatest(0, ordered.start_mileage - ordered.previous_end_mileage) * 0.621371)::integer
  END AS unaccounted_miles
FROM ordered;

COMMENT ON VIEW public.tacho_daily_miles IS
  'Miles projection and positive odometer gaps between records for each truck. Raw kilometres remain unchanged.';

GRANT SELECT ON public.tacho_daily_miles TO tacho_writer;
