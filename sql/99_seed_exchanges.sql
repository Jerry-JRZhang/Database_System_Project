-- Seed the two US exchanges relevant to the S&P 500 universe
INSERT INTO exchange (code, name, country, tz) VALUES
  ('XNYS', 'New York Stock Exchange', 'US', 'America/New_York'),
  ('XNAS', 'Nasdaq Stock Market',     'US', 'America/New_York')
ON CONFLICT (code) DO NOTHING;
