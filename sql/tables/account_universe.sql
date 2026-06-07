CREATE TABLE IF NOT EXISTS `product-analytics-389809.retail_stores.account_universe` (
  store_id        STRING  NOT NULL,

  -- Store identity
  brand           STRING,
  store_name      STRING,
  chain_name      STRING,
  is_chain        BOOL,

  -- Location
  address         STRING,
  house_number    STRING,
  road            STRING,
  parsed_city     STRING,
  state           STRING,
  zip             STRING,
  parsed_country        STRING,
  nj_abc_license_number STRING,
  phone                 STRING,
  email           STRING,

  -- Scraped quality signals
  rating          FLOAT64,
  review_count    INT64,

  -- Distributor matching
  distributor_matches ARRAY<STRUCT<
    distributor_name  STRING,
    matched           BOOL,
    customer_id       STRING,
    customer_name     STRING,
    match_layer       STRING,
    match_confidence  FLOAT64,
    match_status      STRING,
    case_volume       FLOAT64,
    revenue           FLOAT64,
    revenue_per_case  FLOAT64
  >>,
  sarene_flag     BOOL,

  -- Geo (Places API preferred, backfilled from Census geocoder)
  place_id        STRING,
  lat             FLOAT64,
  lng             FLOAT64,

  -- Audit
  matched_at      TIMESTAMP,
  run_date        DATE,

  PRIMARY KEY (store_id) NOT ENFORCED
);
