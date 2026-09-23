# Synthetic PIT fixtures

All five JSON files are hand-authored synthetic examples, not downloaded provider
payloads or claims about an actual issuer/series. The filing examples use a minimal
single-row adaptation of SEC submissions fields. XBRL is a minimal normalized fact
contract, not a full companyfacts parser. ALFRED adds caller-supplied `series_id`
and `vintage_date` to the representative observation fields. Dates and values exist
only to exercise temporal policies. No external provider is contacted by tests.
Official format references are documented in `docs/point-in-time-data.md`.
