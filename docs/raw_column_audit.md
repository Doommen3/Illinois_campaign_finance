# Raw Column Audit

_Generated: 2026-02-16 16:39:45Z_

## 1) Executive summary

- total_feeds: 37
- total_unique_raw_columns: 859
- count_columns_dropped: 408
- count_columns_never_loaded: 124
- count_columns_loaded_but_unused: 317

## Step 0 — feed inventory

| feed_name | raw_location(s) | file_format(s) | ingestion_script(s) |
|---|---|---|---|
| ISBE bulk TXT (legacy bulk loader) | `Bulk_download/*.txt (committees_, d2totals_, candidates_, cmtecandidatelinks_, receipts_, expenditures_)` | TSV | `cli/commands.py::import_bulk_download_command -> database/bulk_download_loader.py::import_bulk_download` |
| ISBE bulk TXT (sunshine ETL) | `Bulk_download/*.txt (all 12 ISBE files)` | TSV | `cli/commands.py::sunshine_import_command -> scripts/isbe_sunshine_etl.py` |
| IL SOS lobbying CSV | `Bulk_download/ILSOS_Lobbying_activeandclients/*.csv; Bulk_download/Lobbyist_Entity_Client_Data_Daily_*.csv` | CSV | `cli/commands.py::import_lobbying_command -> database/lobbying_loader.py::load_lobbying_csv` |
| IRS 527 FullDataFile | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | Pipe-delimited (headerless, record-typed) | `cli/commands.py::import_irs527_command -> database/irs527_loader.py::load_irs527_full_file` |
| Chicago Socrata Phase 1 | `Runtime API: data.cityofchicago.org resource/*.json` | JSON (API) | `cli/commands.py::import_chicago_phase1_command -> database/chicago_loader.py::import_chicago_phase1` |
| FEC API | `Runtime API: api.open.fec.gov/v1/* (cached in raw_extractions)` | JSON (API) | `cli/commands.py::sync_fec_il_federal_command/backfill_* -> database/federal_fec.py` |
| OpenBook Illinois Comptroller | `Runtime HTML pages + parsed payload snapshots in raw_extractions` | HTML/JSON | `cli/commands.py::import_openbook_batch_command -> scraper/openbook_scraper.py` |
| ISBE web scrapers (main/detail/committee/D2) | `Runtime HTML pages (not persisted as full raw snapshots)` | HTML | `cli/commands.py::scrape_main/scrape_details/scrape_committee_reports/scrape_d2_*` |
| Comptroller contracts scraper (module) | `Runtime HTML pages (tests fixtures only in repo)` | HTML | `scraper/comptroller_contracts.py::ComptrollerContractsScraper` |

## Step 1 — raw-file census evidence

Method: `python3 scripts/raw_schema_census.py --output-json output/raw_schema_census.json` (all delimited files scanned for headers; row profiling sampled first N rows per file; IRS record types scanned full-file and profiled per record type; runtime JSON feeds scanned from `raw_extractions`; Chicago datasets scanned from Socrata metadata plus sampled API rows).

### isbe_committees_tsv

- raw files: `Bulk_download/Committees.txt`, `Bulk_download/committees_639060038163077225.txt`
- union_of_columns_count: 27
- intersection_of_columns_count: 27

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Address1` | 2 | yes | 6301 N Sheridan Rd \| 1075 N Marshfield Ave \| 2742 W. North Ave | 0.000296 |  |
| `Address2` | 2 | yes | Ste 15-G \| 435 E 35th St \| 500 E 33rd St Apt 1515 | 0.804469 |  |
| `Address3` | 2 | yes | Attn Mark Flood \| #93 \| Unit #2 | 0.995055 |  |
| `CanSuppOpp` | 2 | yes | S \| O | 0.169843 |  |
| `City` | 2 | yes | Chicago \| Beecher \| Kankakee | 0.000000 |  |
| `CreationAmount` | 2 | yes | 0 \| 52966.7 \| 500 | 0.000000 |  |
| `CreationDate` | 2 | yes | 2000-08-21 00:00:00 \| 2007-08-13 00:00:00 \| 2011-12-09 00:00:00 | 0.080250 |  |
| `DispFunds95` | 2 | yes | False \| True | 0.000000 |  |
| `DispFundsCharity` | 2 | yes | False \| True | 0.000000 |  |
| `DispFundsDescrip` | 2 | yes | Cook County Democratic Party \| tbd \| The Democratic Party Of Illinois | 0.651831 |  |
| `DispFundsPolComm` | 2 | yes | True \| False | 0.000000 |  |
| `DispFundsReturn` | 2 | yes | False \| True | 0.000000 |  |
| `ID` | 2 | yes | 15504 \| 21057 \| 24042 | 0.000000 |  |
| `LocalCommittee` | 2 | yes | True \| False | 0.000000 |  |
| `LocalID` | 2 | yes | 10040 \| 14554 \| 0 | 0.000000 |  |
| `Name` | 2 | yes | Citizens for.com PAC \| 1st Ward Democratic Committeeman Fund \| 1st Ward First Independent Democratic Org | 0.000000 |  |
| `PartyAffiliation` | 2 | yes | Democratic \| Republican \| Libertarian | 0.533988 |  |
| `PolicySuppOpp` | 2 | yes | S \| O | 0.808896 |  |
| `Purpose` | 2 | yes | To support candidates and elected officials that support legislation that benefits the internet. \| To elect and support Democratic candidates for public and political office. \| To elect qualified candidates who represent the interests of 1st ward residents | 0.080694 |  |
| `ReferName` | 2 | yes | .com PAC \| 0001 \| 0001 Ward | 0.000000 |  |
| `State` | 2 | yes | IL \| RI \| Il | 0.000000 |  |
| `StateCommittee` | 2 | yes | True \| False | 0.000000 |  |
| `StateID` | 2 | yes | 7775 \| 9692 \| 0 | 0.000000 |  |
| `Status` | 2 | yes | F \| A | 0.000000 |  |
| `StatusDate` | 2 | yes | 2001-12-17 00:00:00 \| 2016-08-17 00:00:00 \| 2019-01-30 00:00:00 | 0.000000 |  |
| `TypeOfCommittee` | 2 | yes | Political Party \| Political Action \| Ballot Initiative | 0.561157 |  |
| `Zip` | 2 | yes | 60660 \| 60622 \| 60647-6535 | 0.000000 |  |

### isbe_d2totals_tsv

- raw files: `Bulk_download/D2Totals.txt`, `Bulk_download/d2totals_639060039113297425.txt`
- union_of_columns_count: 31
- intersection_of_columns_count: 31

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Archived` | 2 | yes | False \| True | 0.000000 |  |
| `BegFundsAvail` | 2 | yes | 1000 \| 18.73 \| 11.65 | 0.000000 |  |
| `CommitteeID` | 2 | yes | 10125 \| 1371 \| 1239 | 0.000000 |  |
| `DebtsI` | 2 | yes | 0 \| 85557.25 \| 1153 | 0.000000 |  |
| `DebtsNI` | 2 | yes | 0 \| 785.72 \| 147.58 | 0.000000 |  |
| `EndFundsAvail` | 2 | yes | 308 \| 0 \| 137.85 | 0.000000 |  |
| `ExpendI` | 2 | yes | 0 \| 221302.67 \| 2413 | 0.000000 |  |
| `ExpendNI` | 2 | yes | 0 \| 7.2 \| 11.65 | 0.000000 |  |
| `FiledDocID` | 2 | yes | 0 \| 4 \| 5 | 0.000000 |  |
| `ID` | 2 | yes | 52635 \| 1258 \| 1241 | 0.000000 |  |
| `InKindI` | 2 | yes | 0 \| 49.15 \| 14574.82 | 0.000000 |  |
| `InKindNI` | 2 | yes | 0 \| 152.86 \| 86.03 | 0.000000 |  |
| `IndependentExpI` | 2 | yes | 0 | 0.000000 |  |
| `IndependentExpNI` | 2 | yes | 0 | 0.000000 |  |
| `IndivContribI` | 2 | yes | 690 \| 0 \| 110060 | 0.000000 |  |
| `IndivContribNI` | 2 | yes | 0 \| 200 \| 18740.5 | 0.000000 |  |
| `LoanMadeI` | 2 | yes | 0 \| 2000 \| 2500 | 0.000000 |  |
| `LoanMadeNI` | 2 | yes | 0 \| 190.84 \| 699.6 | 0.000000 |  |
| `LoanRcvI` | 2 | yes | 1200 \| 0 \| 90000 | 0.000000 |  |
| `LoanRcvNI` | 2 | yes | 0 \| 133.75 \| 147.58 | 0.000000 |  |
| `OtherRctI` | 2 | yes | 0 \| 474.5 \| 233.83 | 0.000000 |  |
| `OtherRctNI` | 2 | yes | 0 \| 150 \| 49.12 | 0.000000 |  |
| `TotalDebts` | 2 | yes | 0 \| 85557.25 \| 1153 | 0.000000 |  |
| `TotalExpend` | 2 | yes | 2582 \| 218.73 \| 11.65 | 0.000000 |  |
| `TotalInKind` | 2 | yes | 0 \| 49.15 \| 14574.82 | 0.000000 |  |
| `TotalInvest` | 2 | yes | 0 | 0.000000 |  |
| `TotalReceipts` | 2 | yes | 1890 \| 200 \| 0 | 0.000000 |  |
| `XferInI` | 2 | yes | 0 \| 1750 \| 1700 | 0.000000 |  |
| `XferInNI` | 2 | yes | 0 \| 975 \| 25 | 0.000000 |  |
| `XferOutI` | 2 | yes | 2582 \| 211.53 \| 0 | 0.000000 |  |
| `XferOutNI` | 2 | yes | 0 \| 836 \| 220 | 0.000000 |  |

### isbe_candidates_tsv

- raw files: `Bulk_download/Candidates.txt`, `Bulk_download/candidates_639060037834639303.txt`
- union_of_columns_count: 14
- intersection_of_columns_count: 14

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Address1` | 2 | yes | 512 N Paulina St \| 215 Wing Park Boulevard \| 104 Anne Ct | 0.079289 |  |
| `Address2` | 2 | yes | Suite 200 \| 7339 N Tripp Avenue \| Apt A | 0.968123 |  |
| `City` | 2 | yes | Chicago \| Elgin \| Thornton | 0.073004 |  |
| `District` | 2 | yes | 1 \| Elgin \| Thornton | 0.012664 |  |
| `DistrictType` | 2 | yes | Ward \| City \| Village | 0.000000 |  |
| `FirstName` | 2 | yes | Lauren \| Christina "Tia" \| Richard | 0.000000 |  |
| `ID` | 2 | yes | 46284 \| 46519 \| 2812 | 0.000000 |  |
| `LastName` | 2 | yes | Young Weber \| Aagesen \| Aardsma | 0.000000 |  |
| `Office` | 2 | yes | Alderperson \| Councilman \| Trustee | 0.000000 |  |
| `PartyAffiliation` | 2 | yes | Non Partisan \| Democratic \| Independent | 0.219027 |  |
| `RedactionRequested` | 2 | yes | False \| True | 0.000000 |  |
| `ResidenceCounty` | 2 | yes | Cook \| Saline \| Kane | 0.031706 |  |
| `State` | 2 | yes | IL \| Il \| L | 0.070117 |  |
| `Zip` | 2 | yes | 60622 \| 60123 \| 60476 | 0.098145 |  |

### isbe_cmte_candidate_links_tsv

- raw files: `Bulk_download/CmteCandidateLinks.txt`, `Bulk_download/cmtecandidatelinks_639060047756941723.txt`
- union_of_columns_count: 3
- intersection_of_columns_count: 3

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `CandidateID` | 2 | yes | 344 \| 253 \| 4652 | 0.000000 |  |
| `CommitteeID` | 2 | yes | 2 \| 4 \| 5 | 0.000000 |  |
| `ID` | 2 | yes | 105 \| 65 \| 3778 | 0.000000 |  |

### isbe_receipts_tsv

- raw files: `Bulk_download/Receipts.txt`, `Bulk_download/receipts_639060048226403871.txt`
- union_of_columns_count: 29
- intersection_of_columns_count: 29

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Address1` | 2 | yes | Rt 1 Box 255 \| 16 Bruarckuff \| 221 Liberty St | 0.000000 |  |
| `Address2` | 2 | yes | Po Box 397 \| Rr 1 Dimmick Twp \| Po Box 3932 | 0.907880 |  |
| `AggregateAmount` | 2 | yes | 0 | 0.000000 |  |
| `Amount` | 2 | yes | 500 \| 250 \| 150 | 0.000000 |  |
| `Archived` | 2 | yes | False \| True | 0.000000 |  |
| `City` | 2 | yes | Decatur \| Bourbonnais \| Morris | 0.001060 |  |
| `CommitteeID` | 2 | yes | 10353 \| 10720 \| 10682 | 0.000000 |  |
| `Country` | 2 | yes |  | 1.000000 |  |
| `D2Part` | 2 | yes | 2A \| 1A \| 5A | 0.000000 |  |
| `Description` | 2 | yes | Food For Fundraiser \| Copier For Office \| Insurance For Office | 0.896620 |  |
| `ETransID` | 2 | yes |  | 1.000000 |  |
| `Employer` | 2 | yes |  | 1.000000 |  |
| `FiledDocID` | 2 | yes | 82298 \| 82667 \| 83074 | 0.000000 |  |
| `FirstName` | 2 | yes | Donald \| H James \| John & Sandy | 0.638520 |  |
| `ID` | 2 | yes | 236628 \| 236629 \| 236630 | 0.000000 |  |
| `LastOnlyName` | 2 | yes | Abc Pac \| Bacon \| Baum | 0.000000 |  |
| `LoanAmount` | 2 | yes | 0 \| 8066.84 \| 122.6 | 0.000000 |  |
| `Occupation` | 2 | yes |  | 1.000000 |  |
| `RcvDate` | 2 | yes | 1998-10-28 00:00:00 \| 1998-09-03 00:00:00 \| 1998-09-10 00:00:00 | 0.000000 |  |
| `RedactionRequested` | 2 | yes | False \| True | 0.000000 |  |
| `State` | 2 | yes | IL \| NC \| TX | 0.001060 |  |
| `VendorAddress1` | 2 | yes |  | 1.000000 |  |
| `VendorAddress2` | 2 | yes |  | 1.000000 |  |
| `VendorCity` | 2 | yes |  | 1.000000 |  |
| `VendorFirstName` | 2 | yes |  | 1.000000 |  |
| `VendorLastOnlyName` | 2 | yes |  | 1.000000 |  |
| `VendorState` | 2 | yes |  | 1.000000 |  |
| `VendorZip` | 2 | yes |  | 1.000000 |  |
| `Zip` | 2 | yes | 62526 \| 60914 \| 60450 | 0.001060 |  |

### isbe_expenditures_tsv

- raw files: `Bulk_download/Expenditures.txt`, `Bulk_download/expenditures_639065716188368118.txt`
- union_of_columns_count: 23
- intersection_of_columns_count: 23

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Address1` | 2 | yes | 3524 Johnson Rd. \| 5838 W. Cermak Rd. \| P. O. BOX  506 | 0.019520 |  |
| `Address2` | 2 | yes | 314N \| P.O. Box 231 \| P.O BOX 707 | 0.936780 |  |
| `AggregateAmount` | 2 | yes | 300 \| 160 \| 5000 | 0.000000 |  |
| `Amount` | 2 | yes | 300 \| 160 \| 5000 | 0.000000 |  |
| `Archived` | 2 | yes | True \| False | 0.000000 |  |
| `CandidateName` | 2 | yes |  | 1.000000 |  |
| `City` | 2 | yes | Granitte City \| Cicero \| FRANKLIN  PK. | 0.008800 |  |
| `CommitteeID` | 2 | yes | 12478 \| 4294 \| 7103 | 0.000000 |  |
| `Country` | 2 | yes |  | 1.000000 |  |
| `D2Part` | 2 | yes | 6B \| 8B \| 7B | 0.000000 |  |
| `ETransID` | 2 | yes |  | 1.000000 |  |
| `ExpendedDate` | 2 | yes | 1999-03-01 00:00:00 \| 1999-02-25 00:00:00 \| 1999-03-11 00:00:00 | 0.000000 |  |
| `FiledDocID` | 2 | yes | 169182 \| 170584 \| 171179 | 0.000000 |  |
| `FirstName` | 2 | yes | George \| Ben \| Mark | 0.843300 |  |
| `ID` | 2 | yes | 1267 \| 1268 \| 1269 | 0.000000 |  |
| `LastOnlyName` | 2 | yes | Sykes \| Regular Cicero Democratic Organization \| UNITED - 4 - PROGRESS | 0.000000 |  |
| `Office` | 2 | yes |  | 1.000000 |  |
| `Opposing` | 2 | yes | False | 0.000000 |  |
| `Purpose` | 2 | yes | contribution \| ticket purchase \| TRANSFER  OUT | 0.008580 |  |
| `RedactionRequested` | 2 | yes | False \| True | 0.000000 |  |
| `State` | 2 | yes | IL \| WI \| PA | 0.001540 |  |
| `Supporting` | 2 | yes | False | 0.000000 |  |
| `Zip` | 2 | yes | 62040 \| 60804 \| 60131 | 0.020840 |  |

### isbe_filed_docs_tsv

- raw files: `Bulk_download/FiledDocs.txt`
- union_of_columns_count: 29
- intersection_of_columns_count: 29

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Amend` | 1 | yes | True \| False | 0.000000 |  |
| `Archived` | 1 | yes | False \| True | 0.000000 |  |
| `B9SignerFirstName` | 1 | yes |  | 1.000000 |  |
| `B9SignerLastOnlyName` | 1 | yes |  | 1.000000 |  |
| `Clarification` | 1 | yes |  | 1.000000 |  |
| `Comment` | 1 | yes | Changed Name Of Committee \| Annual And Final Report \| Schedule A Attached | 0.974920 |  |
| `CommitteeID` | 1 | yes | 586 \| 1464 \| 1463 | 0.000000 |  |
| `DocName` | 1 | yes | Statement of Organization \| Final \| Pre-election | 0.000000 |  |
| `ElectionType` | 1 | yes | CE \| GE \| GP | 0.508200 |  |
| `ElectionYear` | 1 | yes | 1989 \| 1988 \| 1990 | 0.508180 |  |
| `FiledDocType` | 1 | yes | 05 \| 30 \| 10 | 0.000000 |  |
| `ID` | 1 | yes | 1 \| 2 \| 3 | 0.000000 |  |
| `Pages` | 1 | yes | 2 \| 0 \| 3 | 0.000000 |  |
| `Provider` | 1 | yes |  | 1.000000 |  |
| `RcvdAt` | 1 | yes | S \| C | 0.000000 |  |
| `RcvdDateTime` | 1 | yes | 1989-05-30 10:54:00 \| 1989-05-30 16:02:00 \| 1989-05-30 10:57:00 | 0.000000 |  |
| `RedactionRequested` | 1 | yes | False | 0.000000 |  |
| `RptPdBegDate` | 1 | yes | 1989-01-07 00:00:00 \| 1988-07-01 00:00:00 \| 1989-01-08 00:00:00 | 0.163360 |  |
| `RptPdEndDate` | 1 | yes | 1989-03-17 00:00:00 \| 1989-05-25 00:00:00 \| 1989-03-05 00:00:00 | 0.163280 |  |
| `SbmttrAddress1` | 1 | yes |  | 1.000000 |  |
| `SbmttrAddress2` | 1 | yes |  | 1.000000 |  |
| `SbmttrCity` | 1 | yes |  | 1.000000 |  |
| `SbmttrFirstName` | 1 | yes |  | 1.000000 |  |
| `SbmttrLastOnlyName` | 1 | yes |  | 1.000000 |  |
| `SbmttrState` | 1 | yes |  | 1.000000 |  |
| `SbmttrZip` | 1 | yes |  | 1.000000 |  |
| `SignerFirstName` | 1 | yes |  | 1.000000 |  |
| `SignerLastOnlyName` | 1 | yes |  | 1.000000 |  |
| `Source` | 1 | yes | D | 0.000000 |  |

### isbe_canelections_tsv

- raw files: `Bulk_download/CanElections.txt`
- union_of_columns_count: 9
- intersection_of_columns_count: 9

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `CandidateID` | 1 | yes | 1 \| 2 \| 3 | 0.000000 |  |
| `ElectionType` | 1 | yes | GP \| GE \| CE | 0.000000 |  |
| `ElectionYear` | 1 | yes | 1988 \| 1992 \| 1996 | 0.000000 |  |
| `FairCampaign` | 1 | yes | False \| True | 0.000000 |  |
| `ID` | 1 | yes | 1 \| 2 \| 3 | 0.000000 |  |
| `IncChallOpen` | 1 | yes | Inc \| Chal \| Open | 0.323260 |  |
| `LimitsOff` | 1 | yes | False \| True | 0.000000 |  |
| `LimitsOffReason` | 1 | yes | Limits Off for Race \| Board Determination \| Self Funding Disclosure | 0.996940 |  |
| `WonLost` | 1 | yes | Won \| Lost \| Nob | 0.054840 |  |

### isbe_officers_tsv

- raw files: `Bulk_download/Officers.txt`
- union_of_columns_count: 11
- intersection_of_columns_count: 11

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Address1` | 1 | yes | 435 W Erie Street # 1204 \| 744 Lenox Ln \| 215 Wing Park Boulevard | 0.001960 |  |
| `Address2` | 1 | yes | 20 N Wacker Dr, Ste 2275 \| #6C \| Unit 2 | 0.927400 |  |
| `City` | 1 | yes | Chicago \| Glenview \| Elgin | 0.006300 |  |
| `FirstName` | 1 | yes | Christopher \| Mary Lou \| Christina | 0.000040 |  |
| `ID` | 1 | yes | 77192 \| 804 \| 87269 | 0.000000 |  |
| `LastName` | 1 | yes | !saac \| Aagaard \| Aagesen | 0.000000 |  |
| `Phone` | 1 | yes | 312.929.6406 \| 618/253-3602 \| 312-332-7132 | 0.284900 |  |
| `RedactionRequested` | 1 | yes | False \| True | 0.000000 |  |
| `State` | 1 | yes | IL \| PA \| DC | 0.006300 |  |
| `Title` | 1 | yes | Treasurer \| Chair \| Secretary | 0.000000 |  |
| `Zip` | 1 | yes | 60654 \| 60025 \| 60123 | 0.017080 |  |

### isbe_prev_officers_tsv

- raw files: `Bulk_download/PrevOfficers.txt`
- union_of_columns_count: 12
- intersection_of_columns_count: 12

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Address1` | 1 | yes | 10034 S. Carpenter \| 11152 S. Lowe \| 19w055 Avenue Normandy South | 0.002554 |  |
| `Address2` | 1 | yes | 2nd Floor North \| Second Floor North \| Unit #5 | 0.902830 |  |
| `City` | 1 | yes | Chicago \| Oak Brook \| Lombard | 0.004689 |  |
| `CommitteeID` | 1 | yes | 1 \| 5 \| 8 | 0.000000 |  |
| `FirstName` | 1 | yes | Sylvia \| Nelson \| Hugh R. | 0.000042 |  |
| `ID` | 1 | yes | 6707 \| 6708 \| 114 | 0.000000 |  |
| `LastName` | 1 | yes | Brooks \| Rice \| Murphy | 0.000084 |  |
| `RedactionRequested` | 1 | yes | False \| True | 0.000000 |  |
| `ResignDate` | 1 | yes | 1998-12-31 12:05:33 \| 1998-12-31 12:05:53 \| 1989-10-06 00:00:00 | 0.000000 |  |
| `State` | 1 | yes | IL \| Il \| iL | 0.004689 |  |
| `Title` | 1 | yes | Treasurer \| Chair \| Vice-Chair | 0.000000 |  |
| `Zip` | 1 | yes | 60643 \| 60628 \| 60521 | 0.015448 |  |

### isbe_cmte_officer_links_tsv

- raw files: `Bulk_download/CmteOfficerLinks.txt`
- union_of_columns_count: 3
- intersection_of_columns_count: 3

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `CommitteeID` | 1 | yes | 1 \| 2 \| 3 | 0.000000 |  |
| `ID` | 1 | yes | 23999 \| 24000 \| 3 | 0.000000 |  |
| `OfficerID` | 1 | yes | 26668 \| 26669 \| 2 | 0.000000 |  |

### isbe_investments_tsv

- raw files: `Bulk_download/Investments.txt`
- union_of_columns_count: 19
- intersection_of_columns_count: 19

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `Address1` | 1 | yes | 5069 N. Broadway \| 201 E. Cherry St. \| Rose & Franklin Aves. | 0.021395 |  |
| `Address2` | 1 | yes | Suite 1000 \| p o box 33030 \| P O Box 89 | 0.882264 |  |
| `Archived` | 1 | yes | False \| True | 0.000000 |  |
| `City` | 1 | yes | Chicago \| Watseka \| Franklin Park | 0.021311 |  |
| `CommitteeID` | 1 | yes | 4294 \| 320 \| 4682 | 0.000000 |  |
| `Country` | 1 | yes | United States \| Cook \| Will | 0.986032 |  |
| `CurrentValue` | 1 | yes | 5000 \| 100000 \| 35000 | 0.000000 |  |
| `Description` | 1 | yes | Purchase CD \| purchase of CD \| Certificate of Deposit | 0.000000 |  |
| `FiledDocID` | 1 | yes | 173016 \| 173563 \| 173583 | 0.000000 |  |
| `FirstName` | 1 | yes | Banda \| Quin \| Beverly | 0.962780 |  |
| `ID` | 1 | yes | 22 \| 23 \| 24 | 0.000000 |  |
| `LastOnlyName` | 1 | yes | International Bank of Chicago \| Iroquois Federal Savings \| LaSalle Bank | 0.002532 |  |
| `LiquidDate` | 1 | yes | 1999-06-30 00:00:00 \| 1999-12-31 00:00:00 \| 1999-09-27 00:00:00 | 0.902097 |  |
| `LiquidValue` | 1 | yes | 10000 \| 0 \| 56859.8 | 0.000000 |  |
| `PurchaseDate` | 1 | yes | 1998-07-29 00:00:00 \| 1998-04-01 00:00:00 \| 1999-01-29 00:00:00 | 0.000000 |  |
| `PurchasePrice` | 1 | yes | 15000 \| 100000 \| 35000 | 0.000000 |  |
| `PurchaseShares` | 1 | yes | 0 | 0.299996 |  |
| `State` | 1 | yes | IL \| PA \| MA | 0.002785 |  |
| `Zip` | 1 | yes | 60640 \| 60970 \| 60131 | 0.023843 |  |

### ilsos_lobbying_active_clients_csv

- raw files: `Bulk_download/ILSOS_Lobbying_activeandclients/Active_Lobbying_Entities_and_Their_Clients_20260213.csv`
- union_of_columns_count: 5
- intersection_of_columns_count: 5

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `CLIENT_ID` | 1 | yes | 8799 \| 7167 \| 10608 | 0.000000 |  |
| `CLIENT_NAME` | 1 | yes | FOX VALLEY PARK DISTRICT \| UNIVERSITY OF ILLINOIS \| VILLAGE OF LINCOLNWOOD | 0.000000 |  |
| `ENTITY_ID` | 1 | yes | 7846 \| 4683 \| 11375 | 0.000000 |  |
| `ENTITY_NAME` | 1 | yes | ADVANTAGE GOVERNMENT STRATEGIES \| ALEXANDER, BOROVICKA & O'SHEA GOVERNMENT SOLUTIONS \| ALIVIO MEDICAL CENTER | 0.000000 |  |
| `ENT_REG_YEAR` | 1 | yes | 2026 \| 2025 \| 2024 | 0.000000 |  |

### ilsos_lobbying_daily_csv

- raw files: `Bulk_download/Lobbyist_Entity_Client_Data_Daily_20260214.csv`
- union_of_columns_count: 28
- intersection_of_columns_count: 28

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `CLIENT_ADDR1` | 1 | yes | 600 SOUTH SECOND STREET \| C/O POLITICOM LAW LLP \| 333 WEST 35TH STREET | 0.223244 |  |
| `CLIENT_ADDR2` | 1 | yes | SUITE 400 \| 28 LIBERTY SHIP WAY, SUITE 2815 \| 14TH FLOOR | 0.731358 |  |
| `CLIENT_CITY` | 1 | yes | SPRINGFIELD \| SAUSALITO \| CHICAGO | 0.223244 |  |
| `CLIENT_ID` | 1 | yes | 0 \| 1022 \| 7652 | 0.000000 |  |
| `CLIENT_NAME` | 1 | yes | MARQUARDT, ROGER C. & COMPANY, INC. \| SALESFORCE.COM, INC. \| CHICAGO WHITE SOX | 0.223244 |  |
| `CLIENT_STATUS` | 1 | yes | ACTIVE \| TERMINATED | 0.223244 |  |
| `CLIENT_ST_ABBR` | 1 | yes | IL \| CA \| NY | 0.223244 |  |
| `CLIENT_ZIP` | 1 | yes | 62704 \| 94965 \| 60616 | 0.223685 |  |
| `ENT_ADDR1` | 1 | yes | 3M CENTER, BUILDING 220-9E-02 \| 1000 CHURCHILL RD, P.O. BOX 2328 \| 150 N. MICHIGAN AVENUE | 0.000000 |  |
| `ENT_ADDR2` | 1 | yes | SUITE 600 \| SUITE 1A \| DEPT. G812-BLDG. AP6D-2 | 0.483282 |  |
| `ENT_CITY` | 1 | yes | ST. PAUL \| SPRINGFIELD \| CHICAGO | 0.000000 |  |
| `ENT_ID` | 1 | yes | 2 \| 10 \| 63 | 0.000000 |  |
| `ENT_NAME` | 1 | yes | 3M COMPANY \| AFSCME COUNCIL 31 \| AMERICAN CIVIL LIBERTIES UNION | 0.000000 |  |
| `ENT_REG_YEAR` | 1 | yes | 2025 \| 2026 \| 2022 | 0.000000 |  |
| `ENT_ST_ABBR` | 1 | yes | MN \| IL \| TX | 0.000000 |  |
| `ENT_ZIP` | 1 | yes | 55144 \| 62702 \| 60601 | 0.000000 |  |
| `LOBBYIST_ADDR1` | 1 | yes | 3M CENTER \| 3M CENTER, BUILDING 220-9E-02 \| 1000 CHURCHILL RD | 0.000000 |  |
| `LOBBYIST_ADDR2` | 1 | yes | BUILDING 220-9E-01 \| SUITE 600 \| STE. 1A | 0.598103 |  |
| `LOBBYIST_CITY` | 1 | yes | ST. PAUL \| CHICAGO \| SPRINGFIELD | 0.000000 |  |
| `LOBBYIST_EMAIL` | 1 | yes | MATT.HEDBERG@YAHOO.COM \| TCLARK@MMM.COM \| AALEXANDER@AFSCME31.ORG | 0.000000 |  |
| `LOBBYIST_FNAME` | 1 | yes | MATTHEW \| TYLER \| ADRIENNE | 0.000000 |  |
| `LOBBYIST_ID` | 1 | yes | 7370 \| 13446 \| 7517 | 0.000000 |  |
| `LOBBYIST_LNAME` | 1 | yes | HEDBERG \| CLARK \| ALEXANDER | 0.000000 |  |
| `LOBBYIST_MNAME` | 1 | yes | C \| W \| D | 0.638084 |  |
| `LOBBYIST_PHONE` | 1 | yes | 5179839944 \| 6089990603 \| 3126416060 | 0.000000 |  |
| `LOBBYIST_STATUS` | 1 | yes | TERMINATED \| ACTIVE \| NONCOMPLIANT | 0.000000 |  |
| `LOBBYIST_ST_ABBR` | 1 | yes | MN \| IL \| AZ | 0.000000 |  |
| `LOBBYIST_ZIP` | 1 | yes | 55144 \| 62702 \| 60601 | 0.000000 |  |

### irs527_full_data_record_1_org_registration

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 43
- intersection_of_columns_count: 39
- record_type: `1`
- rows_observed: 75273

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 8871 | 0.000000 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 8 \| 9 \| 10 | 0.000000 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | 0 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes | 0 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_5` | 1 | yes | 0 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_6` | 1 | yes | 912121950 \| 954857244 \| 061596525 | 0.000000 | Headerless feed: positional column index. |
| `pos_7` | 1 | yes | Dan Swecker for Senate Campaign \| FRIENDS OF TOM CALDERON \| ASGM PAC | 0.000000 | Headerless feed: positional column index. |
| `pos_8` | 1 | yes | 10420 - 173rd Ave SW \| 728 W. EDNA PLACE \| WILLIAM S WEBB CO INC | 0.000000 | Headerless feed: positional column index. |
| `pos_9` | 1 | yes | 377 OAK ST - CS601 \| Suite 110 \| 41st Floor | 0.823600 | Headerless feed: positional column index. |
| `pos_10` | 1 | yes | Rochester \| COVINA \| GARDEN CITY | 0.000000 | Headerless feed: positional column index. |
| `pos_11` | 1 | yes | WA \| CA \| NY | 0.000000 | Headerless feed: positional column index. |
| `pos_12` | 1 | yes | 98579 \| 91722 \| 11530 | 0.000000 | Headerless feed: positional column index. |
| `pos_13` | 1 | yes | 0601 \| 0806 \| 0754 | 0.774360 | Headerless feed: positional column index. |
| `pos_14` | 1 | yes | wfga@localaccess.com \| no@email \| HERB10424@AOL.COM | 0.000000 | Headerless feed: positional column index. |
| `pos_15` | 1 | yes | 19701209 \| 20010709 \| 19990913 | 0.136480 | Headerless feed: positional column index. |
| `pos_16` | 1 | yes | Dan Swecker \| YOLANDA MIRANDA \| RICHARD C BLIVEN | 0.000000 | Headerless feed: positional column index. |
| `pos_17` | 1 | yes | 10420 - 173rd Ave SW \| 728 W. EDNA PLACE \| 377 OAK ST - CS601 | 0.000020 | Headerless feed: positional column index. |
| `pos_18` | 1 | yes | Suite 110 \| Suite G \| 41st Floor | 0.818160 | Headerless feed: positional column index. |
| `pos_19` | 1 | yes | Rochester \| COVINA \| GARDEN CITY | 0.000020 | Headerless feed: positional column index. |
| `pos_20` | 1 | yes | WA \| CA \| NY | 0.000020 | Headerless feed: positional column index. |
| `pos_21` | 1 | yes | 98579 \| 91722 \| 11530 | 0.000020 | Headerless feed: positional column index. |
| `pos_22` | 1 | yes | 0601 \| 0806 \| 5457 | 0.786560 | Headerless feed: positional column index. |
| `pos_23` | 1 | yes | Dan Swecker \| YOLANDA MIRANDA \| RICHARD C BLIVEN | 0.000000 | Headerless feed: positional column index. |
| `pos_24` | 1 | yes | 10420 - 173rd Ave. SW \| 728 W. EDNA PLACE \| 377 OAK ST - CS601 | 0.000020 | Headerless feed: positional column index. |
| `pos_25` | 1 | yes | Suite 110 \| Suite G \| 201 | 0.819600 | Headerless feed: positional column index. |
| `pos_26` | 1 | yes | Rochester \| COVINA \| GARDEN CITY | 0.000000 | Headerless feed: positional column index. |
| `pos_27` | 1 | yes | WA \| CA \| NY | 0.000000 | Headerless feed: positional column index. |
| `pos_28` | 1 | yes | 98579 \| 91722 \| 11530 | 0.000000 | Headerless feed: positional column index. |
| `pos_29` | 1 | yes | 0601 \| 0806 \| 5457 | 0.785460 | Headerless feed: positional column index. |
| `pos_30` | 1 | yes | same \| 728 W. EDNA PLACE \| WILLIAM S WEBB CO INC | 0.000100 | Headerless feed: positional column index. |
| `pos_31` | 1 | yes | 377 OAK ST - CS601 \| Suite 110 \| 41st Floor | 0.822860 | Headerless feed: positional column index. |
| `pos_32` | 1 | yes | Rochester \| COVINA \| GARDEN CITY | 0.000100 | Headerless feed: positional column index. |
| `pos_33` | 1 | yes | WA \| CA \| NY | 0.000200 | Headerless feed: positional column index. |
| `pos_34` | 1 | yes | 98579 \| 91722 \| 11530 | 0.000200 | Headerless feed: positional column index. |
| `pos_35` | 1 | yes | 0601 \| 0806 \| 4572 | 0.790240 | Headerless feed: positional column index. |
| `pos_36` | 1 | yes | 0 \| 1 | 0.139340 | Headerless feed: positional column index. |
| `pos_37` | 1 | yes | WY \| ND \| OR | 0.406220 | Headerless feed: positional column index. |
| `pos_38` | 1 | yes | 0 \| 1 | 0.139340 | Headerless feed: positional column index. |
| `pos_39` | 1 | yes | Tax exempt political organization - Political campaign \| RAISE FUNDS TO ELECT CANDIDATE \| POLITICAL ACTION COMMITTEE TO PROMOTE LEGISLATION FAVORABLE TO WORKERS COMPENSATION SAFETY GROUPS IN NEW YORK STATE. | 0.000320 | Headerless feed: positional column index. |
| `pos_40` | 1 | yes | 20021202 \| 20020901 \| 20021203 | 0.391820 | Headerless feed: positional column index. |
| `pos_41` | 1 | yes | 2001-05-13 21:20:54 \| 2001-05-14 15:05:31 \| 2001-05-14 17:24:52 | 0.011000 | Headerless feed: positional column index. |
| `pos_42` | 1 | yes | 0 \| 1 | 0.011060 | Headerless feed: positional column index. |
| `pos_43` | 1 | yes | 1 \| 0 | 0.011060 | Headerless feed: positional column index. |

### irs527_full_data_record_2_periodic_report

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 49
- intersection_of_columns_count: 49
- record_type: `2`
- rows_observed: 54020

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 8872 | 0.000000 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 9555268 \| 9556563 \| 9557324 | 0.000000 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | 20030101 \| 20030701 \| 20040101 | 0.000000 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes | 20030630 \| 20031231 \| 20040331 | 0.000000 | Headerless feed: positional column index. |
| `pos_5` | 1 | yes | 1 \| 0 | 0.000000 | Headerless feed: positional column index. |
| `pos_6` | 1 | yes | 0 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_7` | 1 | yes | 0 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_8` | 1 | yes | 0 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_9` | 1 | yes | HAWAII STATE TEACHERS ASSOCIATION POLITICAL ACTION COMMITTEE \| NATPAC \| UP POWER A WPS RESOURCES PAC | 0.000000 | Headerless feed: positional column index. |
| `pos_10` | 1 | yes | 521073928 \| 593729315 \| 392037087 | 0.000000 | Headerless feed: positional column index. |
| `pos_11` | 1 | yes | 1200 ALA KAPUNA STREET \| Post Office Box 1701 \| 700 N. ADAMS STREET, P.O. BOX 19002 | 0.000000 | Headerless feed: positional column index. |
| `pos_12` | 1 | yes | SUITE ONE \| 319 \| Suite 306 | 0.769760 | Headerless feed: positional column index. |
| `pos_13` | 1 | yes | HONOLULU \| Tallahassee \| GREEN BAY | 0.000000 | Headerless feed: positional column index. |
| `pos_14` | 1 | yes | HI \| FL \| WI | 0.000000 | Headerless feed: positional column index. |
| `pos_15` | 1 | yes | 96819 \| 32302 \| 54307 | 0.000000 | Headerless feed: positional column index. |
| `pos_16` | 1 | yes | 1701 \| 9002 \| 2565 | 0.812440 | Headerless feed: positional column index. |
| `pos_17` | 1 | yes | lhasegawa@nea.org \| mherron@nettally.com \| ssoderl@wpsr.com | 0.000000 | Headerless feed: positional column index. |
| `pos_18` | 1 | yes | 19701209 \| 20010709 \| 20011105 | 0.000000 | Headerless feed: positional column index. |
| `pos_19` | 1 | yes | LYNNELLE HASEGAWA \| Mark Herron \| JAMES N MORRISON | 0.000000 | Headerless feed: positional column index. |
| `pos_20` | 1 | yes | 1200 ALA KAPUNA STREET \| Post Office Box 1701 \| 700 N ADAMS STREET | 0.000000 | Headerless feed: positional column index. |
| `pos_21` | 1 | yes | SUITE ONE \| 2nd Floor \| Suite 306 | 0.770940 | Headerless feed: positional column index. |
| `pos_22` | 1 | yes | HONOLULU \| Tallahassee, \| GREEN BAY | 0.000000 | Headerless feed: positional column index. |
| `pos_23` | 1 | yes | HI \| FL \| WI | 0.000000 | Headerless feed: positional column index. |
| `pos_24` | 1 | yes | 96819 \| 32302 \| 54307 | 0.000000 | Headerless feed: positional column index. |
| `pos_25` | 1 | yes | 1701 \| 9002 \| 1522 | 0.809200 | Headerless feed: positional column index. |
| `pos_26` | 1 | yes | LYNNELLE HASEGAWA \| Mark Herron \| JAMES N. MORRISON | 0.000000 | Headerless feed: positional column index. |
| `pos_27` | 1 | yes | 1200 ALA KAPUNA STREET \| Post Office Box 1701 \| 700 N ADAMS STREET | 0.000000 | Headerless feed: positional column index. |
| `pos_28` | 1 | yes | SUITE ONE \| Suite 306 \| GR11-251 | 0.771060 | Headerless feed: positional column index. |
| `pos_29` | 1 | yes | HONOLULU \| Tallahassee \| GREEN BAY | 0.000000 | Headerless feed: positional column index. |
| `pos_30` | 1 | yes | HI \| FL \| WI | 0.000000 | Headerless feed: positional column index. |
| `pos_31` | 1 | yes | 96819 \| 32302 \| 54307 | 0.000000 | Headerless feed: positional column index. |
| `pos_32` | 1 | yes | 1701 \| 9002 \| 1522 | 0.810680 | Headerless feed: positional column index. |
| `pos_33` | 1 | yes | 1200 ALA KAPUNA STREET \| Post Office Box 1701 \| 700 N. ADAMS STREET, P.O. BOX 19002 | 0.000060 | Headerless feed: positional column index. |
| `pos_34` | 1 | yes | SUITE ONE \| 319 \| Suite 306 | 0.772580 | Headerless feed: positional column index. |
| `pos_35` | 1 | yes | HONOLULU \| Tallahassee \| GREEN BAY | 0.000060 | Headerless feed: positional column index. |
| `pos_36` | 1 | yes | HI \| FL \| WI | 0.000060 | Headerless feed: positional column index. |
| `pos_37` | 1 | yes | 96819 \| 32302 \| 54307 | 0.000060 | Headerless feed: positional column index. |
| `pos_38` | 1 | yes | 1701 \| 9002 \| 1522 | 0.821340 | Headerless feed: positional column index. |
| `pos_39` | 1 | yes | 5 \| 4 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_40` | 1 | yes | 02 \| 03 \| 01 | 0.817520 | Headerless feed: positional column index. |
| `pos_41` | 1 | yes | primary \| general \| General | 0.938360 | Headerless feed: positional column index. |
| `pos_42` | 1 | yes | 20040918 \| 20041102 \| 20021105 | 0.840160 | Headerless feed: positional column index. |
| `pos_43` | 1 | yes | HI \| FL \| AZ | 0.840320 | Headerless feed: positional column index. |
| `pos_44` | 1 | yes | 0 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_45` | 1 | yes | 97298 \| 91018 \| 46113 | 0.000000 | Headerless feed: positional column index. |
| `pos_46` | 1 | yes | 0 \| 1 | 0.000000 | Headerless feed: positional column index. |
| `pos_47` | 1 | yes | 500 \| 1150 \| 0 | 0.000000 | Headerless feed: positional column index. |
| `pos_48` | 1 | yes | 2003-07-08 21:30:24 \| 2004-01-23 19:29:51 \| 2004-04-08 19:03:41 | 0.000000 | Headerless feed: positional column index. |
| `pos_49` | 1 | yes |  | 1.000000 | Headerless feed: positional column index. |

### irs527_full_data_record_A_contribution

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 17
- intersection_of_columns_count: 5
- record_type: `A`
- rows_observed: 9319992

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 9555268 \| 9556563 \| 9557324 | 0.000000 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 38296 \| 38297 \| 38298 | 0.000000 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | HAWAII STATE TEACHERS ASSOCIATION POLITICAL ACTION COMMITTEE \| NATPAC \| UP POWER A WPS RESOURCES PAC | 0.000000 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes | 521073928 \| 593729315 \| 392037087 | 0.000000 | Headerless feed: positional column index. |
| `pos_5` | 1 | yes | National Education Association \| Hawaii State Teachers Association \| HAWAII STATE TEACHERS ASSOCIATION | 0.000000 | Headerless feed: positional column index. |
| `pos_6` | 1 | yes | 1201 16th Street, NW \| 1200 ALA KAPUNA STREET \| 445 North Wymore Road | 0.000040 | Headerless feed: positional column index. |
| `pos_7` | 1 | yes | BOX 45 \| Box 45 \| Suite 100 | 0.858740 | Headerless feed: positional column index. |
| `pos_8` | 1 | yes | Washington \| HONOLULU \| Winter Park | 0.000000 | Headerless feed: positional column index. |
| `pos_9` | 1 | yes | DC \| HI \| FL | 0.000000 | Headerless feed: positional column index. |
| `pos_10` | 1 | yes | 20036 \| 96819 \| 32789 | 0.000000 | Headerless feed: positional column index. |
| `pos_11` | 1 | yes | 3290 \| 1522 \| 0000 | 0.768220 | Headerless feed: positional column index. |
| `pos_12` | 1 | yes | n/a \| N/A \| Same | 0.000000 | Headerless feed: positional column index. |
| `pos_13` | 1 | yes | 5000 \| 15083 \| 15301 | 0.000000 | Headerless feed: positional column index. |
| `pos_14` | 1 | yes | n/a \| N/A \| Trade Association | 0.000000 | Headerless feed: positional column index. |
| `pos_15` | 1 | yes | 5000 \| 92298 \| 77215 | 0.000000 | Headerless feed: positional column index. |
| `pos_16` | 1 | yes | 20030214 \| 20030630 \| 20030531 | 0.331080 | Headerless feed: positional column index. |
| `pos_17` | 1 | yes |  | 1.000000 | Headerless feed: positional column index. |

### irs527_full_data_record_B_expenditure

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 17
- intersection_of_columns_count: 5
- record_type: `B`
- rows_observed: 7778883

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 9555268 \| 9556563 \| 9558209 | 0.000000 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 38295 \| 71358 \| 71359 | 0.000000 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | HAWAII STATE TEACHERS ASSOCIATION POLITICAL ACTION COMMITTEE \| NATPAC \| UP POWER A WPS RESOURCES PAC | 0.000000 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes | 521073928 \| 593729315 \| 392037087 | 0.000000 | Headerless feed: positional column index. |
| `pos_5` | 1 | yes | Hawaii Republican Party \| GOP HOUSE PAC \| CITIZENS FOR RESPONSIVE GOVERNMENT | 0.000000 | Headerless feed: positional column index. |
| `pos_6` | 1 | yes | 725 Kapiolani Blvd. \| CARE OF - \| 770 KAPIOLANI BLVD | 0.000220 | Headerless feed: positional column index. |
| `pos_7` | 1 | yes | C105 \| 2650 PACIFIC HEIGHTS RD \| STE 505 | 0.879540 | Headerless feed: positional column index. |
| `pos_8` | 1 | yes | Honolulu \| HONOLULU \| KANEOHE | 0.000000 | Headerless feed: positional column index. |
| `pos_9` | 1 | yes | HI \| FL \| MI | 0.000000 | Headerless feed: positional column index. |
| `pos_10` | 1 | yes | 96813 \| 96819 \| 96744 | 0.000000 | Headerless feed: positional column index. |
| `pos_11` | 1 | yes | 3031 \| 1522 \| 9267 | 0.933340 | Headerless feed: positional column index. |
| `pos_12` | 1 | yes | n/a \| N/A \| SELF EMPLOYED | 0.000000 | Headerless feed: positional column index. |
| `pos_13` | 1 | yes | 500 \| 750 \| 400 | 0.000000 | Headerless feed: positional column index. |
| `pos_14` | 1 | yes | n/a \| N/A \| POLITICIAN | 0.000000 | Headerless feed: positional column index. |
| `pos_15` | 1 | yes | 20030430 \| 20030911 \| 20031218 | 0.306860 | Headerless feed: positional column index. |
| `pos_16` | 1 | yes | Contribution \| REPUBLICAN FUNDRAISER \| DEMOCRATIC FUNDRAISER | 0.306880 | Headerless feed: positional column index. |
| `pos_17` | 1 | yes |  | 1.000000 | Headerless feed: positional column index. |

### irs527_full_data_record_D_director

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 13
- intersection_of_columns_count: 5
- record_type: `D`
- rows_observed: 184466

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 8 \| 9 \| 10 | 0.000000 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 26174 \| 269 \| 270 | 0.000000 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | Dan Swecker for Senate Campaign \| FRIENDS OF TOM CALDERON \| ASGM PAC | 0.000000 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes | 912121950 \| 954857244 \| 061596525 | 0.000000 | Headerless feed: positional column index. |
| `pos_5` | 1 | yes | Dan Swecker \| THOMAS M. CALDERON \| YOLANDA MIRAND | 0.000000 | Headerless feed: positional column index. |
| `pos_6` | 1 | yes | Candidate \| CANDIDATE \| TREASURER | 0.000020 | Headerless feed: positional column index. |
| `pos_7` | 1 | yes | 10420 - 173rd Ave. SW \| 412 N. 10TH STREET \| 728 W. EDNA PLACE | 0.000140 | Headerless feed: positional column index. |
| `pos_8` | 1 | yes | 125 MAIDEN LANE \| 11 BEECH STREET \| 550 MAMARONECK AV-STE 405 | 0.801480 | Headerless feed: positional column index. |
| `pos_9` | 1 | yes | Rochester \| MONTEBELLO \| COVINA | 0.000000 | Headerless feed: positional column index. |
| `pos_10` | 1 | yes | WA \| CA \| NY | 0.000000 | Headerless feed: positional column index. |
| `pos_11` | 1 | yes | 98579 \| 90640 \| 91722 | 0.000000 | Headerless feed: positional column index. |
| `pos_12` | 1 | yes | 0601 \| 0806 \| 5457 | 0.839360 | Headerless feed: positional column index. |
| `pos_13` | 1 | yes |  | 1.000000 | Headerless feed: positional column index. |

### irs527_full_data_record_E_election_authority

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 5
- intersection_of_columns_count: 5
- record_type: `E`
- rows_observed: 17305

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 9555171 \| 9555173 \| 9555181 | 0.000000 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 1365 \| 2 \| 3 | 0.000000 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | 01945 \| 00136142-50 \| 34541 | 0.000000 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes | FL \| MI \| NY | 0.000000 | Headerless feed: positional column index. |
| `pos_5` | 1 | yes |  | 1.000000 | Headerless feed: positional column index. |

### irs527_full_data_record_H_header

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 4
- intersection_of_columns_count: 4
- record_type: `H`
- rows_observed: 1

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 20260208 | 0.000000 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 0641 | 0.000000 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | F | 0.000000 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes |  | 1.000000 | Headerless feed: positional column index. |

### irs527_full_data_record_R_related_org

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 13
- intersection_of_columns_count: 5
- record_type: `R`
- rows_observed: 64224

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 10 \| 25 \| 31 | 0.000000 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 5 \| 7 \| 8 | 0.000000 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | ASGM PAC \| Brookhaven Town Republican Pre-Primary Committee \| BLUE DOG NON-FEDERAL PAC | 0.000000 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes | 061596525 \| 112794844 \| 522316473 | 0.000000 | Headerless feed: positional column index. |
| `pos_5` | 1 | yes | ASSOCIATION OF SAFETY GROUPS \| Brookhaven Town Republican Committee \| Blue Dog Political Action Committee | 0.000000 | Headerless feed: positional column index. |
| `pos_6` | 1 | yes | Connected \| Federal Account \| Self | 0.000000 | Headerless feed: positional column index. |
| `pos_7` | 1 | yes | WILLIAM S WEBB CO INC \| 1717 N. Ocean Avenue \| P.O. Box 7668 | 0.000040 | Headerless feed: positional column index. |
| `pos_8` | 1 | yes | 377 OAK ST - CS0601 \| 1100 \| R-300 | 0.821020 | Headerless feed: positional column index. |
| `pos_9` | 1 | yes | GARDEN CITY \| Medford \| Washington | 0.000020 | Headerless feed: positional column index. |
| `pos_10` | 1 | yes | NY \| DC \| MO | 0.000000 | Headerless feed: positional column index. |
| `pos_11` | 1 | yes | 11530 \| 11763 \| 20044 | 0.000000 | Headerless feed: positional column index. |
| `pos_12` | 1 | yes | 0601 \| 2343 \| 1845 | 0.684800 | Headerless feed: positional column index. |
| `pos_13` | 1 | yes |  | 1.000000 | Headerless feed: positional column index. |

### irs527_full_data_record_UNKNOWN

- raw files: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- union_of_columns_count: 12
- intersection_of_columns_count: 0
- record_type: `UNKNOWN`
- rows_observed: 10718

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `pos_1` | 1 | yes | 20130306 \| 20130116 \| 20130320 | 0.890931 | Headerless feed: positional column index. |
| `pos_2` | 1 | yes | 2013-03-06 16:32:00 \| 2013-03-18 11:11:29 \| 2013-03-20 14:49:05 | 0.806027 | Headerless feed: positional column index. |
| `pos_3` | 1 | yes | 1 \| 0 \| 775 | 0.810319 | Headerless feed: positional column index. |
| `pos_4` | 1 | yes | 1 \| 0 \| 20091019 | 0.811159 | Headerless feed: positional column index. |
| `pos_5` | 1 | yes | N/A \| WESTERN REFINING, INC. \| RETIRED | 0.999160 | Headerless feed: positional column index. |
| `pos_6` | 1 | yes | N/A \| Brian Sims \| STATE OF ARKANSAS | 0.913790 | Headerless feed: positional column index. |
| `pos_7` | 1 | yes | 10 \| 3973 \| 195 | 0.913697 | Headerless feed: positional column index. |
| `pos_8` | 1 | yes | N/A \| Chief of Staff \| DEPUTY DIRECTOR OF LEGISLATIVE AFFAIRS | 0.913697 | Headerless feed: positional column index. |
| `pos_9` | 1 | yes | 20150130 \| 20150617 \| 20150108 | 0.913697 | Headerless feed: positional column index. |
| `pos_10` | 1 | yes | AMEX/TRAVEL \| REIMBURSEMENT; METRO, TRAVEL \| INSURANCE | 0.914443 | Headerless feed: positional column index. |
| `pos_11` | 1 | yes | Political contribution | 0.999907 | Headerless feed: positional column index. |
| `pos_12` | 1 | yes |  | 1.000000 | Headerless feed: positional column index. |

### fec_api_schedule_a_json

- raw files: `raw_extractions:fec_api:schedules_schedule_a:f13d47ec7c4732c98634b886fd9979289d42d0d1`, `raw_extractions:fec_api:schedules_schedule_a:98e38b95be9eb31749a3ff52d676d0078676e4d1`, `raw_extractions:fec_api:schedules_schedule_a:820ee583d6f695a607715fde9236a00e6fc10d61`, `raw_extractions:fec_api:schedules_schedule_a:2d89850b7d91c9f74db2196f1a07860219e324cf`, `raw_extractions:fec_api:schedules_schedule_a:440608d0054e46e524ef222748e33c225959c381`, `raw_extractions:fec_api:schedules_schedule_a:0dbf760a699f91678d9afe0d73835c1ed5efb6b4`, `raw_extractions:fec_api:schedules_schedule_a:e8bf15e5d29b8fb902a0d383784faa35bd40b3b3`, `raw_extractions:fec_api:schedules_schedule_a:7e9fecf8d677dc328747fd881afe7f57f6ae2bd0`, `... (+1677 more)`
- union_of_columns_count: 81
- intersection_of_columns_count: 0

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `amendment_indicator` | 1249 | no | A \| N \| C | 0.000000 |  |
| `amendment_indicator_desc` | 1249 | no | ADD \| NO CHANGE \| CHANGE | 0.000000 |  |
| `back_reference_schedule_name` | 1249 | no | SA11AI \| SA11D \| SA12 | 0.587340 |  |
| `back_reference_transaction_id` | 1249 | no | A7ABDE7206C224F078A4 \| AD2A07F1A69DA436CAB2 \| AA57A013D938E4152891 | 0.580720 |  |
| `candidate_first_name` | 1249 | no | JONATHAN \| FELIX \| JEFFREY D | 0.989060 |  |
| `candidate_id` | 1249 | no | S6IL00623 \| H6IL07479 \| H6IL11166 | 0.989240 |  |
| `candidate_last_name` | 1249 | no | DEAN \| TELLO \| WALTER | 0.989060 |  |
| `candidate_middle_name` | 1249 | no | MCKAY \| L \| D. | 0.997800 |  |
| `candidate_name` | 1249 | no | DEAN, JONATHAN \| TELLO, FELIX \| WALTER, JEFFREY D | 0.989060 |  |
| `candidate_office` | 1249 | no | S \| H | 0.989060 |  |
| `candidate_office_district` | 1249 | no | 00 \| 07 \| 11 | 0.991160 |  |
| `candidate_office_full` | 1249 | no | SENATE \| HOUSE | 0.989060 |  |
| `candidate_office_state` | 1249 | no | IL \| TX \| IN | 0.989060 |  |
| `candidate_office_state_full` | 1249 | no | ILLINOIS \| TEXAS \| INDIANA | 0.989060 |  |
| `candidate_prefix` | 1249 | no | DR. \| MRS. \| MR. | 0.998400 |  |
| `candidate_suffix` | 1249 | no | JR \| JR. | 0.997880 |  |
| `committee` | 1249 | no | {'affiliated_committee_name': 'NONE', 'candidate_ids': ['H6IL17262'], 'city': 'MT CARROLL', 'committee_id': 'C00917245', 'committee_type': 'H', 'committee_type_full': 'House', 'cycle': 2026, 'cycles': [2026], 'cycles_has_activity': [2026], 'cycles_has_financial': [2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2025-08-26', 'first_file_date': '2025-08-26', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-08-26', 'last_file_date': '2026-01-30', 'name': 'JULIE FOR ILLINOIS', 'organization_type': None, 'organization_type_full': None, 'party': 'REP', 'party_full': 'REPUBLICAN PARTY', 'state': 'IL', 'state_full': 'Illinois', 'street_1': '15835 US-52', 'street_2': None, 'treasurer_name': 'DATWYLER, THOMAS C', 'zip': '61053'} \| {'affiliated_committee_name': 'NONE', 'candidate_ids': ['S6IL00623'], 'city': 'CHICAGO', 'committee_id': 'C00915280', 'committee_type': 'S', 'committee_type_full': 'Senate', 'cycle': 2026, 'cycles': [2026], 'cycles_has_activity': [2026], 'cycles_has_financial': [2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2025-08-08', 'first_file_date': '2025-08-08', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-08-08', 'last_file_date': '2026-01-15', 'name': 'DEAN FOR ILLINOIS', 'organization_type': None, 'organization_type_full': None, 'party': 'DEM', 'party_full': 'DEMOCRATIC PARTY', 'state': 'IL', 'state_full': 'Illinois', 'street_1': '105 W MADISON STREET', 'street_2': 'SUITE 1300', 'treasurer_name': 'DEAN, JONATHAN', 'zip': '60602'} \| {'affiliated_committee_name': 'NONE', 'candidate_ids': ['H6IL07479'], 'city': 'CHICAGO', 'committee_id': 'C00917930', 'committee_type': 'H', 'committee_type_full': 'House', 'cycle': 2026, 'cycles': [2026], 'cycles_has_activity': [2026], 'cycles_has_financial': [2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2025-08-30', 'first_file_date': '2025-08-30', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-10-21', 'last_file_date': '2025-10-21', 'name': 'FELIX FOR CONGRESS', 'organization_type': None, 'organization_type_full': None, 'party': 'DEM', 'party_full': 'DEMOCRATIC PARTY', 'state': 'IL', 'state_full': 'Illinois', 'street_1': 'PO BOX 107', 'street_2': None, 'treasurer_name': 'TELLO, FELIX', 'zip': '60690'} | 0.000000 |  |
| `committee_id` | 1249 | no | C00917245 \| C00915280 \| C00917930 | 0.000000 |  |
| `committee_name` | 1249 | no |  | 1.000000 |  |
| `conduit_committee_city` | 1249 | no | ARLINGTON \| WASHINGTON \| CHICAGO | 0.982240 |  |
| `conduit_committee_id` | 1249 | no |  | 1.000000 |  |
| `conduit_committee_name` | 1249 | no | WINRED PAC \| HOUSE FREEDOM FUND \| WINRED | 0.982240 |  |
| `conduit_committee_state` | 1249 | no | VA \| DC \| IL | 0.982240 |  |
| `conduit_committee_street1` | 1249 | no | PO BOX 9891 \| 300 INDEPENDENCE AVENUE SE \| 4250 FAIRFAX DR | 0.982240 |  |
| `conduit_committee_street2` | 1249 | no | STE 600 \| SUITE 530 | 0.996960 |  |
| `conduit_committee_zip` | 1249 | no | 22219 \| 20003 \| 22203 | 0.982240 |  |
| `contribution_receipt_amount` | 1249 | no | 260.25 \| 250.0 \| 500.0 | 0.000000 |  |
| `contribution_receipt_date` | 1249 | no | 2025-09-26 \| 2025-09-25 \| 2025-09-23 | 0.000000 |  |
| `contributor` | 1249 | no | {'affiliated_committee_name': 'NONE', 'candidate_ids': [], 'city': 'ARLINGTON', 'committee_id': 'C00694323', 'committee_type': 'V', 'committee_type_full': 'Hybrid PAC (with Non-Contribution Account) - Nonqualified', 'cycle': 2026, 'cycles': [2020, 2022, 2024, 2026], 'cycles_has_activity': [2020, 2022, 2024, 2026], 'cycles_has_financial': [2020, 2022, 2024, 2026], 'designation': 'U', 'designation_full': 'Unauthorized', 'filing_frequency': 'Q', 'first_f1_date': '2019-01-18', 'first_file_date': '2019-01-18', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2019-10-16', 'last_file_date': '2026-02-09', 'name': 'WINRED', 'organization_type': None, 'organization_type_full': None, 'party': None, 'party_full': None, 'state': 'VA', 'state_full': 'Virginia', 'street_1': '4250 FAIRFAX DR', 'street_2': 'STE 600', 'treasurer_name': 'OTTENHOFF, BENJAMIN', 'zip': '22203'} \| {'affiliated_committee_name': 'A BRTHD AIMED TOWARD EDUC OF IL FED ELEC DEVICE FOR POLITICALLY ACTIVE MOTORCYCLISTS...', 'candidate_ids': [], 'city': 'LINDENHURST', 'committee_id': 'C00308460', 'committee_type': 'Q', 'committee_type_full': 'PAC - Qualified', 'cycle': 2026, 'cycles': [1996, 1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_activity': [1996, 1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_financial': [1996, 1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'designation': 'U', 'designation_full': 'Unauthorized', 'filing_frequency': 'Q', 'first_f1_date': '1995-11-20', 'first_file_date': '1995-11-20', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2024-01-26', 'last_file_date': '2026-01-23', 'name': 'A BRTHD AIMED TOWARD EDUC OF IL FED ELEC DEVICE FOR POLITICALLY ACTIVE MOTORCYCLISTS...', 'organization_type': 'M', 'organization_type_full': 'Membership Organization', 'party': 'NAT', 'party_full': None, 'state': 'IL', 'state_full': 'Illinois', 'street_1': '2807 FALLING WATERS DR', 'street_2': None, 'treasurer_name': 'WINTERS, ELIZABETH', 'zip': '60046'} \| {'affiliated_committee_name': 'BLUE TO THE FUTURE 2024', 'candidate_ids': [], 'city': 'SOMERVILLE', 'committee_id': 'C00401224', 'committee_type': 'V', 'committee_type_full': 'Hybrid PAC (with Non-Contribution Account) - Nonqualified', 'cycle': 2026, 'cycles': [2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_activity': [2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_financial': [2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'designation': 'U', 'designation_full': 'Unauthorized', 'filing_frequency': 'M', 'first_f1_date': '2004-05-17', 'first_file_date': '2004-05-17', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2024-07-29', 'last_file_date': '2026-01-31', 'name': 'ACTBLUE', 'organization_type': None, 'organization_type_full': None, 'party': None, 'party_full': None, 'state': 'MA', 'state_full': 'Massachusetts', 'street_1': 'PO BOX 441146', 'street_2': None, 'treasurer_name': 'GILMER, GEORGE', 'zip': '02144'} | 0.316920 |  |
| `contributor_aggregate_ytd` | 1249 | no | 5507.08 \| 260.25 \| 250.0 | 0.000000 |  |
| `contributor_city` | 1249 | no | ARLINGTON \| LEROY \| THOMSON | 0.000040 |  |
| `contributor_employer` | 1249 | no | SELF \| RETIRED \| COMPEER FINANCIAL | 0.425060 |  |
| `contributor_first_name` | 1249 | no | DAVID \| JANICE \| CYNTHIA | 0.421240 |  |
| `contributor_id` | 1249 | no | C00694323 \| C00308460 \| C00401224 | 0.316500 |  |
| `contributor_last_name` | 1249 | no | OBRIEN \| DURWARD \| WOESSNER | 0.421240 |  |
| `contributor_middle_name` | 1249 | no | R. \| R \| MICHAEL | 0.883920 |  |
| `contributor_name` | 1249 | no | WINRED \| OBRIEN, DAVID \| DURWARD, JANICE | 0.000000 |  |
| `contributor_occupation` | 1249 | no | FARMER \| FARMING \| RETIRED | 0.107900 |  |
| `contributor_prefix` | 1249 | no | MS \| MR \| MRS | 0.987880 |  |
| `contributor_state` | 1249 | no | VA \| IL \| IN | 0.000100 |  |
| `contributor_street_1` | 1249 | no | 4250 FAIRFAX DR \| 22463 E 200 N RD \| 16369B ARGO FAY RTE | 0.000120 |  |
| `contributor_street_2` | 1249 | no | STE 600 \| SUITE 1300 \| 2R | 0.879380 |  |
| `contributor_suffix` | 1249 | no | JR \| III \| SR | 0.991160 |  |
| `contributor_zip` | 1249 | no | 222031665 \| 61752 \| 612857670 | 0.000260 |  |
| `donor_committee_name` | 1249 | no | WINRED \| FRIENDS OF LAUZEN \| AURORA TOWNSHIP REPUBLICAN CENTRAL | 0.683680 |  |
| `election_type` | 1249 | no | P2026 \| G2026 \| G2024 | 0.000200 |  |
| `election_type_full` | 1249 | no | 03/09/2025 \| DEBT PRIMARY 2008 | 0.999880 |  |
| `entity_type` | 1249 | no | ORG \| IND \| CAN | 0.000000 |  |
| `entity_type_desc` | 1249 | no | ORGANIZATION \| INDIVIDUAL \| CANDIDATE | 0.000000 |  |
| `fec_election_type_desc` | 1249 | no | PRIMARY \| GENERAL | 0.000320 |  |
| `fec_election_year` | 1249 | no | 2026 \| 2024 \| 2025 | 0.001260 |  |
| `file_number` | 1249 | no | 1919887 \| 1933129 \| 1928743 | 0.000000 |  |
| `filing_form` | 1249 | no | F3 | 0.000000 |  |
| `image_number` | 1249 | no | 202510159790862035 \| 202510159790862030 \| 202510159790862038 | 0.000000 |  |
| `increased_limit` | 1249 | no |  | 1.000000 |  |
| `is_individual` | 1249 | no | False \| True | 0.000000 |  |
| `line_number` | 1249 | no | 11AI \| 13A \| 14 | 0.000000 |  |
| `line_number_label` | 1249 | no | Contributions From Individuals/Persons Other Than Political Committees \| Loans Received from the Candidate \| Offsets to Operating Expenditures | 0.000000 |  |
| `link_id` | 1249 | no | 4101520251292819260 \| 4011620261302505013 \| 4121720251298943002 | 0.000000 |  |
| `load_date` | 1249 | no | 2025-12-06T03:06:06 \| 2026-01-24T03:06:22 \| 2026-01-14T03:06:11 | 0.000000 |  |
| `memo_code` | 1249 | no | X | 0.588620 |  |
| `memo_code_full` | 1249 | no |  | 1.000000 |  |
| `memo_text` | 1249 | no | TOTAL EARMARKED THROUGH CONDUIT. PAC LIMIT NOT AFFECTED. \| NOTE: ABOVE CONTRIBUTION EARMARKED THROUGH THIS ORGANIZATION. \| * EARMARKED CONTRIBUTION: SEE BELOW | 0.274260 |  |
| `memoed_subtotal` | 1249 | no | True \| False | 0.000000 |  |
| `national_committee_nonfederal_account` | 1249 | no |  | 1.000000 |  |
| `original_sub_id` | 1249 | no |  | 1.000000 |  |
| `pdf_url` | 1249 | no | https://docquery.fec.gov/cgi-bin/fecimg/?202510159790862035 \| https://docquery.fec.gov/cgi-bin/fecimg/?202510159790862030 \| https://docquery.fec.gov/cgi-bin/fecimg/?202510159790862038 | 0.000000 |  |
| `receipt_type` | 1249 | no | 15E \| 15 \| 16C | 0.388500 |  |
| `receipt_type_desc` | 1249 | no | EARMARKED CONTRIBUTION \| CONTRIBUTION \| LOANS RECEIVED FROM THE CANDIDATE | 0.388500 |  |
| `receipt_type_full` | 1249 | no | INTERMEDIARY \| EARMARKED (NON-DIRECTED) THROUGH WINRED \| IN-KIND - | 0.914640 |  |
| `recipient_committee_designation` | 1249 | no | P | 0.000000 |  |
| `recipient_committee_org_type` | 1249 | no |  | 1.000000 |  |
| `recipient_committee_type` | 1249 | no | H \| S | 0.000000 |  |
| `report_type` | 1249 | no | Q3 \| YE \| Q1 | 0.000000 |  |
| `report_year` | 1249 | no | 2025 | 0.000000 |  |
| `schedule_type` | 1249 | no | SA | 0.000000 |  |
| `schedule_type_full` | 1249 | no | ITEMIZED RECEIPTS | 0.000000 |  |
| `sub_id` | 1249 | no | 4120520251297265476 \| 4120520251297265475 \| 4120520251297265461 | 0.000000 |  |
| `transaction_id` | 1249 | no | A05B0F6B9E14F449A861 \| A7ABDE7206C224F078A4 \| A475C98A5333A49B7B4F | 0.000000 |  |
| `two_year_transaction_period` | 1249 | no | 2026 | 0.000000 |  |
| `unused_contbr_id` | 1249 | no | C00694323 \| C00308460 \| C00401224 | 0.316400 |  |

### fec_api_schedule_b_json

- raw files: `raw_extractions:fec_api:schedules_schedule_b:77776af4a1253dcb860ccd91bc6e9aee651aa364`, `raw_extractions:fec_api:schedules_schedule_b:f59a74a4b53e5228bf447792b5e76ce6f8712711`, `raw_extractions:fec_api:schedules_schedule_b:e0ba05f103c9be180cfbaf3e8952134912950040`, `raw_extractions:fec_api:schedules_schedule_b:becbbc3d5ccf47e9784a2490d0c73dfc28d79837`, `raw_extractions:fec_api:schedules_schedule_b:6acf10b9056f190d1d5f4b3768e7205d0237b6d7`, `raw_extractions:fec_api:schedules_schedule_b:f7bf9d9ee46761f6b3c5981f0382f01a0cdfcf70`, `raw_extractions:fec_api:schedules_schedule_b:3acfb2eaa92e3651dc3316cd2dd8c80a2cb3558f`, `raw_extractions:fec_api:schedules_schedule_b:c5be4fd9a5bd84c50499dc0284d32ff3d71e0ffd`, `... (+313 more)`
- union_of_columns_count: 80
- intersection_of_columns_count: 0

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `amendment_indicator` | 190 | no | A \| N \| C | 0.000000 |  |
| `amendment_indicator_desc` | 190 | no | ADD \| NO CHANGE \| CHANGE | 0.000000 |  |
| `back_reference_schedule_id` | 190 | no | SB17 \| SA11C \| SA11AI | 0.792461 |  |
| `back_reference_transaction_id` | 190 | no | 500390958 \| 500390846 \| 500390845 | 0.783335 |  |
| `beneficiary_committee_name` | 190 | no | BRYAN MAXWELL FOR US SENATE \| DEAN FOR ILLINOIS \| DON TRACY FOR ILLINOIS | 0.948060 |  |
| `candidate_first_name` | 190 | no | ADAM \| KEVIN \| S. RAJA | 0.989538 |  |
| `candidate_id` | 190 | no | S6IL00656 \| S6IL00623 \| S6IL00607 | 0.982043 |  |
| `candidate_last_name` | 190 | no | DELGADO \| RYAN \| KRISHNAMOORTHI | 0.989538 |  |
| `candidate_middle_name` | 190 | no | T \| RODGER \| ANN | 0.997626 |  |
| `candidate_name` | 190 | no | DELGADO, ADAM \| RYAN, KEVIN \| KRISHNAMOORTHI, S. RAJA | 0.989538 |  |
| `candidate_office` | 190 | no | S \| H | 0.938562 |  |
| `candidate_office_description` | 190 | no | SENATE \| HOUSE | 0.938562 |  |
| `candidate_office_district` | 190 | no | 00 \| 08 \| 14 | 0.947763 |  |
| `candidate_office_state` | 190 | no | IL \| WI \| CA | 0.938562 |  |
| `candidate_office_state_full` | 190 | no | ILLINOIS \| WISCONSIN \| CALIFORNIA | 0.938562 |  |
| `candidate_prefix` | 190 | no | DR. | 0.999703 |  |
| `candidate_suffix` | 190 | no | JR. | 0.999703 |  |
| `category_code` | 190 | no | 006 \| 004 \| 001 | 0.748831 |  |
| `category_code_full` | 190 | no | Campaign Materials \| Advertising Expenses \| Administrative/Salary/Overhead Expenses | 0.748831 |  |
| `comm_dt` | 190 | no |  | 1.000000 |  |
| `committee` | 190 | no | {'affiliated_committee_name': 'NONE', 'candidate_ids': ['S6IL00672'], 'city': 'ALEXANDRIA', 'committee_id': 'C00921759', 'committee_type': 'S', 'committee_type_full': 'Senate', 'cycle': 2026, 'cycles': [2026], 'cycles_has_activity': [2026], 'cycles_has_financial': [2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2025-10-01', 'first_file_date': '2025-10-01', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-10-01', 'last_file_date': '2026-01-19', 'name': 'JEANNIE FOR ILLINOIS', 'organization_type': None, 'organization_type_full': None, 'party': 'REP', 'party_full': 'REPUBLICAN PARTY', 'state': 'VA', 'state_full': 'Virginia', 'street_1': 'PO BOX 26141', 'street_2': None, 'treasurer_name': 'MARSTON, CHRIS', 'zip': '22313'} \| {'affiliated_committee_name': 'NONE', 'candidate_ids': ['S6IL00656'], 'city': 'CHICAGO', 'committee_id': 'C00920348', 'committee_type': 'S', 'committee_type_full': 'Senate', 'cycle': 2026, 'cycles': [2026], 'cycles_has_activity': [2026], 'cycles_has_financial': [2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2025-09-19', 'first_file_date': '2025-09-19', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-09-19', 'last_file_date': '2026-01-30', 'name': 'BOTSFORD FOR ILLINOIS', 'organization_type': None, 'organization_type_full': None, 'party': 'DEM', 'party_full': 'DEMOCRATIC PARTY', 'state': 'IL', 'state_full': 'Illinois', 'street_1': 'PO BOX 14047', 'street_2': None, 'treasurer_name': 'BOTSFORD, STEVE JR.', 'zip': '60614'} \| {'affiliated_committee_name': 'NONE', 'candidate_ids': ['S6IL00649'], 'city': 'URBANA', 'committee_id': 'C00918466', 'committee_type': 'S', 'committee_type_full': 'Senate', 'cycle': 2026, 'cycles': [2026], 'cycles_has_activity': [2026], 'cycles_has_financial': [2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2025-09-03', 'first_file_date': '2025-09-03', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-09-23', 'last_file_date': '2026-01-31', 'name': 'BRYAN MAXWELL FOR US SENATE', 'organization_type': None, 'organization_type_full': None, 'party': 'DEM', 'party_full': 'DEMOCRATIC PARTY', 'state': 'IL', 'state_full': 'Illinois', 'street_1': '504 1/2 EAST ELM ST. APT. 1', 'street_2': None, 'treasurer_name': 'MAXWELL, BRYAN', 'zip': '61802'} | 0.000000 |  |
| `committee_id` | 190 | no | C00921759 \| C00920348 \| C00918466 | 0.000000 |  |
| `conduit_committee_city` | 190 | no | CONCORD \| OAKLAND | 0.999481 |  |
| `conduit_committee_name` | 190 | no | CHECKMATEHCM \| S.E. OWENS & COMPANY | 0.999481 |  |
| `conduit_committee_state` | 190 | no | NH \| CA | 0.999481 |  |
| `conduit_committee_street1` | 190 | no | 287 SOUTH MAIN STREET \| 312 CLAY STREET, #300 | 0.999481 |  |
| `conduit_committee_street2` | 190 | no |  | 1.000000 |  |
| `conduit_committee_zip` | 190 | no | 3301 \| 94607 | 0.999481 |  |
| `disbursement_amount` | 190 | no | 2923.89 \| 3591.25 \| 5000.0 | 0.000000 |  |
| `disbursement_date` | 190 | no | 2025-12-31 \| 2025-12-24 \| 2025-12-23 | 0.000000 |  |
| `disbursement_description` | 190 | no | FUNDRAISING FEES (DEC) \| COMPLIANCE CONSULTING \| ONLINE ADVERTISING | 0.009498 |  |
| `disbursement_purpose_category` | 190 | no | OTHER \| MATERIALS \| TRAVEL | 0.000000 |  |
| `disbursement_type` | 190 | no | 24G \| 22Y \| 22Z | 0.984863 |  |
| `disbursement_type_description` | 190 | no | TRANSFER OUT AFFILIATED \| CONTRIBUTION REFUND TO INDIVIDUAL \| CONTRIBUTION REFUND TO CANDIDATE/COMMITTEE | 0.984863 |  |
| `election_type` | 190 | no | P2026 \| P2025 \| G2026 | 0.113230 |  |
| `election_type_full` | 190 | no | 2026 \| RECOUNT \| OTHER | 0.999629 |  |
| `entity_type` | 190 | no | ORG \| IND \| CAN | 0.000000 |  |
| `entity_type_desc` | 190 | no | ORGANIZATION \| INDIVIDUAL \| CANDIDATE | 0.000000 |  |
| `fec_election_type_desc` | 190 | no | PRIMARY \| GENERAL \| RECOUNT | 0.113230 |  |
| `fec_election_year` | 190 | no | 2026 \| 2025 \| 2022 | 0.113601 |  |
| `file_number` | 190 | no | 1933746 \| 1920886 \| 1921629 | 0.000000 |  |
| `filing_form` | 190 | no | F3 | 0.000000 |  |
| `image_number` | 190 | no | 202601199794075854 \| 202601199794075845 \| 202601199794075850 | 0.000000 |  |
| `line_number` | 190 | no | 17 \| 18 \| 21 | 0.000000 |  |
| `line_number_label` | 190 | no | Operating Expenditures \| Transfers to Other Authorized Committees \| Other Disbursements | 0.000000 |  |
| `link_id` | 190 | no | 4011920261302701032 \| 4101520251292819918 \| 4101620251292847155 | 0.000000 |  |
| `load_date` | 190 | no | 2026-01-28T02:36:04 \| 2025-12-04T02:36:09 \| 2026-01-24T02:36:08 | 0.000000 |  |
| `memo_code` | 190 | no | X | 0.789345 |  |
| `memo_code_full` | 190 | no |  | 1.000000 |  |
| `memo_text` | 190 | no | * \| FEC ID C00167015 \| REIMBURSEMENT - SEE DETAILS, IF ITEMIZED | 0.824071 |  |
| `memoed_subtotal` | 190 | no | False \| True | 0.000000 |  |
| `national_committee_nonfederal_account` | 190 | no |  | 1.000000 |  |
| `original_sub_id` | 190 | no |  | 1.000000 |  |
| `payee_employer` | 190 | no |  | 1.000000 |  |
| `payee_first_name` | 190 | no | BLAKE \| EVAN \| LILLY | 0.848037 |  |
| `payee_last_name` | 190 | no | BLAKE DELCARMEN \| ZHANG \| MAXSON | 0.848037 |  |
| `payee_middle_name` | 190 | no | CHRISTINE \| M \| S. | 0.976701 |  |
| `payee_occupation` | 190 | no |  | 1.000000 |  |
| `payee_prefix` | 190 | no | DR. | 0.996661 |  |
| `payee_suffix` | 190 | no | JR. \| JR | 0.996216 |  |
| `pdf_url` | 190 | no | https://docquery.fec.gov/cgi-bin/fecimg/?202601199794075854 \| https://docquery.fec.gov/cgi-bin/fecimg/?202601199794075845 \| https://docquery.fec.gov/cgi-bin/fecimg/?202601199794075850 | 0.000000 |  |
| `recipient_city` | 190 | no | ARLINGTON \| ALEXANDRIA \| WASHINGTON | 0.003933 |  |
| `recipient_committee` | 190 | no | {'affiliated_committee_name': 'TEAM RAJA VICTORY FUND', 'candidate_ids': ['H6IL08147'], 'city': 'SCHAUMBURG', 'committee_id': 'C00575092', 'committee_type': 'H', 'committee_type_full': 'House', 'cycle': 2026, 'cycles': [2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_activity': [2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_financial': [2016, 2018, 2020, 2022, 2024, 2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2015-04-03', 'first_file_date': '2015-04-03', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2021-07-15', 'last_file_date': '2026-01-31', 'name': 'FRIENDS OF RAJA FOR CONGRESS', 'organization_type': None, 'organization_type_full': None, 'party': 'DEM', 'party_full': 'DEMOCRATIC PARTY', 'state': 'IL', 'state_full': 'Illinois', 'street_1': 'PO BOX 681202', 'street_2': None, 'treasurer_name': 'BALA, MATANGI', 'zip': '60168'} \| {'affiliated_committee_name': 'DNC/STATE PARTY VICTORY FUND', 'candidate_ids': [], 'city': 'CHICAGO', 'committee_id': 'C00167015', 'committee_type': 'Y', 'committee_type_full': 'Party - Qualified', 'cycle': 2026, 'cycles': [1984, 1986, 1988, 1990, 1992, 1994, 1996, 1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_activity': [1984, 1986, 1988, 1990, 1992, 1994, 1996, 1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_financial': [1984, 1986, 1988, 1990, 1992, 1994, 1996, 1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'designation': 'U', 'designation_full': 'Unauthorized', 'filing_frequency': 'M', 'first_f1_date': '1983-05-04', 'first_file_date': '1983-05-04', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-08-28', 'last_file_date': '2026-01-30', 'name': 'DEMOCRATIC PARTY OF ILLINOIS', 'organization_type': None, 'organization_type_full': None, 'party': 'DEM', 'party_full': 'DEMOCRATIC PARTY', 'state': 'IL', 'state_full': 'Illinois', 'street_1': 'PO BOX 10692', 'street_2': None, 'treasurer_name': 'CROKE, PATRICK', 'zip': '60610'} \| {'affiliated_committee_name': 'NONE', 'candidate_ids': [], 'city': 'WASHINGTON', 'committee_id': 'C00888669', 'committee_type': 'V', 'committee_type_full': 'Hybrid PAC (with Non-Contribution Account) - Nonqualified', 'cycle': 2026, 'cycles': [2024, 2026], 'cycles_has_activity': [2024, 2026], 'cycles_has_financial': [2024, 2026], 'designation': 'U', 'designation_full': 'Unauthorized', 'filing_frequency': 'Q', 'first_f1_date': '2024-09-10', 'first_file_date': '2024-09-10', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-02-20', 'last_file_date': '2026-01-31', 'name': 'KEY TO THE KEYSTONE PAC', 'organization_type': None, 'organization_type_full': None, 'party': None, 'party_full': None, 'state': 'DC', 'state_full': 'District Of Columbia', 'street_1': '1030 15TH ST NW', 'street_2': '#404', 'treasurer_name': 'THOMAN, SHAYNE', 'zip': '20005'} | 0.985976 |  |
| `recipient_committee_id` | 190 | no | C00575092 \| C00167015 \| C00888669 | 0.985605 |  |
| `recipient_name` | 190 | no | WINRED TECHNICAL SERVICES \| ELECTION CFO \| REPUBLICANADS.COM | 0.000000 |  |
| `recipient_state` | 190 | no | VA \| DC \| TX | 0.002003 |  |
| `recipient_zip` | 190 | no | 22219 \| 22314 \| 200032493 | 0.004675 |  |
| `ref_disp_excess_flg` | 190 | no |  | 1.000000 |  |
| `report_type` | 190 | no | YE \| Q3 \| Q2 | 0.000000 |  |
| `report_year` | 190 | no | 2025 | 0.000000 |  |
| `schedule_type` | 190 | no | SB | 0.000000 |  |
| `schedule_type_full` | 190 | no | ITEMIZED DISBURSEMENTS | 0.000000 |  |
| `semi_annual_bundled_refund` | 190 | no | 0.00 \| 101.00 \| 3500.00 | 0.000000 |  |
| `spender_committee_designation` | 190 | no | P | 0.000000 |  |
| `spender_committee_org_type` | 190 | no |  | 1.000000 |  |
| `spender_committee_type` | 190 | no | S \| H | 0.000000 |  |
| `sub_id` | 190 | no | 4012720261303861008 \| 4012720261303860982 \| 4012720261303860996 | 0.000000 |  |
| `transaction_id` | 190 | no | SB17.I20 \| SB17.I9 \| SB17.I18 | 0.000000 |  |
| `two_year_transaction_period` | 190 | no | 2026 | 0.000000 |  |
| `unused_recipient_committee_id` | 190 | no | C00918466 \| C00915280 \| C00917120 | 0.941753 |  |

### fec_api_schedule_e_json

- raw files: `raw_extractions:fec_api:schedules_schedule_e:5ff290387436b35d8ea2c20f9dd9e9942e0262ac`, `raw_extractions:fec_api:schedules_schedule_e:555c501902c4a57d6e2073a93decf59d30907f12`, `raw_extractions:fec_api:schedules_schedule_e:25de5e68561efe42a454cc88af0b9b0ec8f6396d`, `raw_extractions:fec_api:schedules_schedule_e:97d8533181eba581850f2965db3e33158d0402c9`, `raw_extractions:fec_api:schedules_schedule_e:89157d0eaa848ee3843a827ba46b65f1049ad788`, `raw_extractions:fec_api:schedules_schedule_e:1f7a39266fd8cd1c906b956cb971e4ec6c379c3d`, `raw_extractions:fec_api:schedules_schedule_e:77f64385f5afb5197bb1f1724449d196a7ebcfb9`, `raw_extractions:fec_api:schedules_schedule_e:276bb163346b1c604e4ba39a7bd5b79d23af621b`, `... (+185 more)`
- union_of_columns_count: 81
- intersection_of_columns_count: 0

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `action_code` | 69 | no | A \| N \| C | 0.000000 |  |
| `action_code_full` | 69 | no | ADD \| NO CHANGE \| CHANGE | 0.000000 |  |
| `amendment_indicator` | 69 | no | N \| A \| T | 0.008044 |  |
| `amendment_number` | 69 | no | 0 \| 1 \| 2 | 0.990583 |  |
| `back_reference_schedule_name` | 69 | no |  | 1.000000 |  |
| `back_reference_transaction_id` | 69 | no |  | 1.000000 |  |
| `candidate` | 69 | no | {'candidate_id': 'H8IL14174', 'idx': 84597, 'two_year_period': '2018'} \| {'candidate_id': 'H8IL14174', 'idx': 84599, 'two_year_period': '2022'} \| {'candidate_id': 'H8IL14174', 'idx': 84598, 'two_year_period': '2020'} | 0.000000 |  |
| `candidate_first_name` | 69 | no | LAUREN A \| LAUREN \| UNDERWOOD | 0.031980 |  |
| `candidate_id` | 69 | no | H8IL14174 \| H6IL08287 \| S6IL00458 | 0.000000 |  |
| `candidate_last_name` | 69 | no | UNDERWOOD \| LAUREN \| TULLY | 0.031980 |  |
| `candidate_middle_name` | 69 | no | A \| A. \| SCOTT | 0.936629 |  |
| `candidate_name` | 69 | no | UNDERWOOD, LAUREN A \| UNDERWOOD, LAUREN \| UNDERWOOD, LAUREN A. | 0.000000 |  |
| `candidate_office` | 69 | no | H \| S | 0.000000 |  |
| `candidate_office_district` | 69 | no | 14 \| 08 \| 00 | 0.000000 |  |
| `candidate_office_state` | 69 | no | IL \| IA \| 10 | 0.000000 |  |
| `candidate_party` | 69 | no | DEM \| REP \| IND | 0.000000 |  |
| `candidate_prefix` | 69 | no | REP. | 0.988425 |  |
| `candidate_suffix` | 69 | no | A | 0.996861 |  |
| `category_code` | 69 | no | 004 \| 011 \| 001 | 0.447911 |  |
| `category_code_full` | 69 | no | Advertising Expenses \| Political Contributions \| Administrative/Salary/Overhead Expenses | 0.448892 |  |
| `committee` | 69 | no | {'affiliated_committee_name': "EMILY'S LIST", 'candidate_ids': [], 'city': 'WASHINGTON', 'committee_id': 'C00473918', 'committee_type': 'O', 'committee_type_full': 'Super PAC (Independent Expenditure-Only)', 'cycle': 2018, 'cycles': [2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_activity': [2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_financial': [2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'designation': 'U', 'designation_full': 'Unauthorized', 'filing_frequency': 'M', 'first_f1_date': '2010-01-22', 'first_file_date': '2010-01-14', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2025-09-16', 'last_file_date': '2026-01-23', 'name': 'WOMEN VOTE!', 'organization_type': None, 'organization_type_full': None, 'party': None, 'party_full': None, 'state': 'DC', 'state_full': 'District Of Columbia', 'street_1': '1800 M STREET, NW', 'street_2': 'STE 375N', 'treasurer_name': 'VAN HALL, LAURIE', 'zip': '20036'} \| {'affiliated_committee_name': None, 'candidate_ids': [], 'city': 'WASHINGTON', 'committee_id': 'C90017492', 'committee_type': 'I', 'committee_type_full': 'Independent expenditure filer (not a committee)', 'cycle': 2018, 'cycles': [2018, 2020, 2022, 2024, 2026], 'cycles_has_activity': [2018, 2020, 2022, 2024, 2026], 'cycles_has_financial': [2018, 2020, 2022, 2024, 2026], 'designation': 'U', 'designation_full': 'Unauthorized', 'filing_frequency': 'Q', 'first_f1_date': None, 'first_file_date': '2018-03-13', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': None, 'last_file_date': '2026-01-30', 'name': 'INDIVISIBLE PROJECT INC.', 'organization_type': None, 'organization_type_full': None, 'party': None, 'party_full': None, 'state': 'DC', 'state_full': 'District Of Columbia', 'street_1': 'PO BOX 43884', 'street_2': None, 'treasurer_name': None, 'zip': '20010'} \| {'affiliated_committee_name': 'NONE', 'candidate_ids': [], 'city': 'WASHINGTON', 'committee_id': 'C00341396', 'committee_type': 'W', 'committee_type_full': 'Hybrid PAC (with Non-Contribution Account) - Qualified', 'cycle': 2018, 'cycles': [1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_activity': [1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'cycles_has_financial': [1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026], 'designation': 'U', 'designation_full': 'Unauthorized', 'filing_frequency': 'Q', 'first_f1_date': '1998-10-29', 'first_file_date': '1998-10-29', 'is_active': True, 'last_cycle_has_activity': 2026, 'last_cycle_has_financial': 2026, 'last_f1_date': '2022-05-12', 'last_file_date': '2026-01-31', 'name': 'MOVEON.ORG POLITICAL ACTION', 'organization_type': None, 'organization_type_full': None, 'party': None, 'party_full': None, 'state': 'DC', 'state_full': 'District Of Columbia', 'street_1': 'PO BOX 96142', 'street_2': None, 'treasurer_name': 'MATZZIE, TOM', 'zip': '200906142'} | 0.000000 |  |
| `committee_id` | 69 | no | C00473918 \| C90017492 \| C00341396 | 0.000000 |  |
| `conduit_committee_city` | 69 | no |  | 1.000000 |  |
| `conduit_committee_id` | 69 | no | C00341396 \| C00489815 \| C00647701 | 0.686678 |  |
| `conduit_committee_name` | 69 | no |  | 1.000000 |  |
| `conduit_committee_state` | 69 | no |  | 1.000000 |  |
| `conduit_committee_street1` | 69 | no |  | 1.000000 |  |
| `conduit_committee_street2` | 69 | no |  | 1.000000 |  |
| `conduit_committee_zip` | 69 | no |  | 1.000000 |  |
| `disbursement_dt` | 69 | no | 2018-09-27 \| 2018-09-26 \| 2018-09-25 | 0.229547 |  |
| `dissemination_date` | 69 | no | 2018-10-01 \| 2018-09-25 \| 2018-09-20 | 0.146753 |  |
| `election_type` | 69 | no | G2018 \| P2018 \| G2022 | 0.001570 |  |
| `election_type_full` | 69 | no | GENERAL \| PRIMARY \| SPECIAL-PRIMARY | 0.902688 |  |
| `expenditure_amount` | 69 | no | 10731.39 \| 245.45 \| 11.11 | 0.000392 |  |
| `expenditure_date` | 69 | no | 2018-09-27 \| 2018-09-26 \| 2018-09-25 | 0.000000 |  |
| `expenditure_description` | 69 | no | MAILHOUSE \| TEXT MESSAGES \| ADVERTISING | 0.000000 |  |
| `file_number` | 69 | no | 1264593 \| 1273201 \| 1278749 | 0.001570 |  |
| `filer_first_name` | 69 | no | CAROLINE \| TOM \| SABRINA | 0.104179 |  |
| `filer_last_name` | 69 | no | FINES \| MATZZIE \| TINES | 0.104375 |  |
| `filer_middle_name` | 69 | no | M. \| A \| S. | 0.817540 |  |
| `filer_prefix` | 69 | no | ROBERT \| MR. \| DR. | 0.991760 |  |
| `filer_suffix` | 69 | no | TREASURER \| ESQ. \| JR | 0.994114 |  |
| `filing_date` | 69 | no | 2018-10-01 \| 2018-10-15 \| 2018-10-23 | 0.000000 |  |
| `filing_form` | 69 | no | F24 \| F5 \| F3X | 0.000000 |  |
| `form_line_number` | 69 | no | F24-24 \| F3X-24 \| F5-24 | 0.102806 |  |
| `image_number` | 69 | no | 201810019124270255 \| 201810159125495776 \| 201810239130801783 | 0.000000 |  |
| `independent_sign_date` | 69 | no | 2018-10-01 \| 2018-10-14 \| 2018-09-25 | 0.086914 |  |
| `independent_sign_name` | 69 | no | FINES, CAROLINE \| MATZZIE, TOM \| TINES, SABRINA | 0.086718 |  |
| `is_notice` | 69 | no | True \| False | 0.000000 |  |
| `line_number` | 69 | no | 24 | 0.102806 |  |
| `link_id` | 69 | no | 4100120181593006162 \| 4101620181599435177 \| 4102320181600406465 | 0.000000 |  |
| `memo_code` | 69 | no | X | 0.969786 |  |
| `memo_code_full` | 69 | no |  | 1.000000 |  |
| `memo_text` | 69 | no | VENDOR: IN-HOUSE PRINTING, HRC \| * \| NON-CONTRIBUTION ACCOUNT | 0.942319 |  |
| `memoed_subtotal` | 69 | no | False \| True | 0.000000 |  |
| `most_recent` | 69 | no | True \| False | 0.000000 |  |
| `notary_commission_expiration_date` | 69 | no |  | 1.000000 |  |
| `notary_sign_date` | 69 | no |  | 1.000000 |  |
| `notary_sign_name` | 69 | no |  | 1.000000 |  |
| `office_total_ytd` | 69 | no | 166576.7 \| 441.45 \| 1358.34 | 0.001570 |  |
| `original_sub_id` | 69 | no | 4121420221636549313 \| 4121420221636549312 \| 4121420221636549309 | 0.998627 |  |
| `payee_city` | 69 | no | ARLINGTON \| SAN FRANCISCO \| MENLO PARK | 0.001766 |  |
| `payee_first_name` | 69 | no | JOSHUA JOHN \| OLGA \| JOSHUA | 0.901118 |  |
| `payee_last_name` | 69 | no | O'CONNOR \| NUNES \| MAREMA | 0.901118 |  |
| `payee_middle_name` | 69 | no |  | 1.000000 |  |
| `payee_name` | 69 | no | DELIVER STRATEGIES, LLC \| HUSTLE, INC. \| FACEBOOK | 0.001570 |  |
| `payee_prefix` | 69 | no |  | 1.000000 |  |
| `payee_state` | 69 | no | VA \| CA \| DC | 0.001766 |  |
| `payee_street_1` | 69 | no | 4301 FAIRFAX DR \| 251 KEARNY ST, STE 300 \| 1 HACKER WAY | 0.002354 |  |
| `payee_street_2` | 69 | no | STE 550 \| STE 1014 \| STE 1300 | 0.628409 |  |
| `payee_suffix` | 69 | no |  | 1.000000 |  |
| `payee_zip` | 69 | no | 222031627 \| 94108 \| 94025 | 0.002551 |  |
| `pdf_url` | 69 | no | https://docquery.fec.gov/cgi-bin/fecimg/?201810019124270255 \| https://docquery.fec.gov/cgi-bin/fecimg/?201810159125495776 \| http://docquery.fec.gov/cgi-bin/fecimg/?201810239130801783 | 0.000000 |  |
| `previous_file_number` | 69 | no | 1264593 \| 1273201 \| 1269266 | 0.007259 |  |
| `report_type` | 69 | no | 48 \| Q3 \| M10 | 0.000000 |  |
| `report_year` | 69 | no | 2018 \| 2021 \| 2020 | 0.000000 |  |
| `schedule_type` | 69 | no | SE \| SE-F57 | 0.000000 |  |
| `schedule_type_full` | 69 | no | ITEMIZED INDEPENDENT EXPENDITURES \| FOR EACH INDEPENDENT EXPENDITURE MADE | 0.000000 |  |
| `sub_id` | 69 | no | 4100120181593013201 \| 4103020181601287210 \| 4112020181618204912 | 0.000000 |  |
| `support_oppose_indicator` | 69 | no | S \| O | 0.001766 |  |
| `transaction_id` | 69 | no | VN7A7ABNMS6 \| F57.4344 \| SE.24970 | 0.009221 |  |

### fec_api_candidates_search_json

- raw files: `raw_extractions:fec_api:candidates_search:30d222d46a0d98eb68dd9d5c7d1474fa0af310e7`, `raw_extractions:fec_api:candidates_search:942e94d3a7c3608a14db2b0e394b2a0d7f744456`, `raw_extractions:fec_api:candidates_search:e78d69a2805fc322e81387b3d2e4799007f4ac70`, `raw_extractions:fec_api:candidates_search:da87a8d4f8477505e504fe00ee27b5aaf88876af`, `raw_extractions:fec_api:candidates_search:8130a8140a97988703c765d0925d4697ca71f089`
- union_of_columns_count: 25
- intersection_of_columns_count: 0

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `active_through` | 3 | no | 2026 \| 2024 \| 2028 | 0.000000 |  |
| `candidate_id` | 3 | no | H6IL16140 \| H8IL05073 \| H2IL05241 | 0.000000 |  |
| `candidate_inactive` | 3 | no | False \| True | 0.000000 |  |
| `candidate_status` | 3 | no | N \| P \| C | 0.000000 |  |
| `cycles` | 3 | no | [2026] \| [2008, 2010, 2018, 2020, 2022, 2024, 2026] \| [2022, 2024, 2026] | 0.000000 |  |
| `district` | 3 | no | 16 \| 05 \| 00 | 0.034351 |  |
| `district_number` | 3 | no | 16 \| 5 \| 0 | 0.034351 |  |
| `election_districts` | 3 | no | ['16'] \| ['05', '05', '05', '05', '05', '05', '05'] \| ['05'] | 0.000000 |  |
| `election_years` | 3 | no | [2026] \| [2008, 2009, 2018, 2020, 2022, 2024, 2026] \| [2022, 2024] | 0.000000 |  |
| `federal_funds_flag` | 3 | no | False | 0.000000 |  |
| `first_file_date` | 3 | no | 2025-08-11 \| 2008-06-05 \| 2025-12-20 | 0.019084 |  |
| `has_raised_funds` | 3 | no | False \| True | 0.000000 |  |
| `inactive_election_years` | 3 | no | [2014] \| [2026] \| [2022] | 0.950382 |  |
| `incumbent_challenge` | 3 | no | C \| O \| I | 0.041985 |  |
| `incumbent_challenge_full` | 3 | no | Challenger \| Open seat \| Incumbent | 0.041985 |  |
| `last_f2_date` | 3 | no | 2025-08-11 \| 2022-07-05 \| 2025-12-20 | 0.019084 |  |
| `last_file_date` | 3 | no | 2025-08-11 \| 2022-07-05 \| 2025-12-20 | 0.019084 |  |
| `load_date` | 3 | no | 2025-09-02T21:03:03 \| 2026-02-05T21:01:18 \| 2025-12-30T21:00:52 | 0.000000 |  |
| `name` | 3 | no | GULLETTE, GARTH WESLEY \| HANSON, TOM \| HANSON, TOMMY | 0.000000 |  |
| `office` | 3 | no | H \| S | 0.000000 |  |
| `office_full` | 3 | no | House \| Senate | 0.000000 |  |
| `party` | 3 | no | REP \| IND \| OTH | 0.000000 |  |
| `party_full` | 3 | no | REPUBLICAN PARTY \| INDEPENDENT \| OTHER | 0.000000 |  |
| `principal_committees` | 3 | no | [] \| [{'affiliated_committee_name': 'NONE', 'candidate_ids': ['H8IL05073'], 'committee_id': 'C00819870', 'committee_type': 'H', 'committee_type_full': 'House', 'cycles': [2022, 2024, 2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2022-07-05', 'first_file_date': '2022-07-05', 'last_f1_date': '2022-07-05', 'last_file_date': '2024-10-23', 'name': 'TOMMY HANSON FOR CONGRESS', 'organization_type': None, 'organization_type_full': None, 'party': 'REP', 'party_full': 'REPUBLICAN PARTY', 'state': 'IL', 'treasurer_name': 'HANSON, TOMMY'}, {'affiliated_committee_name': None, 'candidate_ids': ['H8IL05073'], 'committee_id': 'C00451252', 'committee_type': 'H', 'committee_type_full': 'House', 'cycles': [2008, 2010], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'T', 'first_f1_date': '2008-06-05', 'first_file_date': '2008-06-05', 'last_f1_date': '2008-09-29', 'last_file_date': '2009-03-24', 'name': 'FRIENDS OF TOM HANSON', 'organization_type': None, 'organization_type_full': None, 'party': 'REP', 'party_full': 'REPUBLICAN PARTY', 'state': 'IL', 'treasurer_name': 'ROGER PACELLI'}] \| [{'affiliated_committee_name': 'NONE', 'candidate_ids': ['S6IL00680'], 'committee_id': 'C00931428', 'committee_type': 'S', 'committee_type_full': 'Senate', 'cycles': [2026], 'designation': 'P', 'designation_full': 'Principal campaign committee', 'filing_frequency': 'Q', 'first_f1_date': '2025-12-20', 'first_file_date': '2025-12-20', 'last_f1_date': '2026-01-13', 'last_file_date': '2026-01-29', 'name': 'FRIENDS OF WHITFIELD HARRINGTON', 'organization_type': None, 'organization_type_full': None, 'party': 'IND', 'party_full': 'INDEPENDENT', 'state': 'IL', 'treasurer_name': 'HARRINGTON, WHITFIELD'}] | 0.000000 |  |
| `state` | 3 | no | IL | 0.000000 |  |

### fec_api_candidate_committees_json

- raw files: `raw_extractions:fec_api:candidate_H6IL07529_committees:bc9976d87eb18b5e6ce93fd95803a09c06f244ee`, `raw_extractions:fec_api:candidate_H6IL08337_committees:a2dbe9e92f92c08291bcdc18267e87d27a16058a`, `raw_extractions:fec_api:candidate_S6IL00441_committees:7c2b7d7d2e9c3d8eb43047500cf538044e8e9bdb`, `raw_extractions:fec_api:candidate_H6IL07412_committees:51603c6f77622d4cc73fe0af0d7fa2ec782db2cb`, `raw_extractions:fec_api:candidate_H4IL05130_committees:0f42e322ba94ff66efbf35ae2380990b199393a7`, `raw_extractions:fec_api:candidate_S6IL00672_committees:30059c7632bef34822975bea35e39bf7dee6c7ea`, `raw_extractions:fec_api:candidate_S6IL00656_committees:2a7c06e19af312013c48ab633295cff74143641e`, `raw_extractions:fec_api:candidate_S8IL00215_committees:195db46356f0e5368e795990d97e71a8b5af294d`, `... (+116 more)`
- union_of_columns_count: 59
- intersection_of_columns_count: 0

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `affiliated_committee_name` | 117 | no | NONE \| TEAM RAJA VICTORY FUND \| ROBIN KELLY FOR CONGRESS | 0.025000 |  |
| `candidate_ids` | 117 | no | ['H4WI02070', 'H4WI04290', 'H6IL07412'] \| ['S6IL00672'] \| ['S6IL00656'] | 0.000000 |  |
| `city` | 117 | no | MILWAUKEE \| ALEXANDRIA \| CHICAGO | 0.000000 |  |
| `committee_id` | 117 | no | C00881896 \| C00921759 \| C00920348 | 0.000000 |  |
| `committee_type` | 117 | no | O \| S \| H | 0.000000 |  |
| `committee_type_full` | 117 | no | Super PAC (Independent Expenditure-Only) \| Senate \| House | 0.000000 |  |
| `custodian_city` | 117 | no | MILWAUKEE \| ALEXANDRIA \| CHICAGO | 0.025000 |  |
| `custodian_name_1` | 117 | no | BOONIE \| BRENDA \| STEVE | 0.025000 |  |
| `custodian_name_2` | 117 | no | SHELTONS \| HANKINS \| BOTSFORD | 0.025000 |  |
| `custodian_name_full` | 117 | no | SHELTONS, BOONIE LASHAWN MRS \| HANKINS, BRENDA \| BOTSFORD, STEVE JR. | 0.025000 |  |
| `custodian_name_middle` | 117 | no | LASHAWN \| JAMES \| M. | 0.825000 |  |
| `custodian_name_prefix` | 117 | no | MRS \| DR. \| MS | 0.933333 |  |
| `custodian_name_suffix` | 117 | no | JR. \| II | 0.983333 |  |
| `custodian_name_title` | 117 | no | DESIGNATED AGENT \| ASSISTANT TREASURER \| TREASURER | 0.025000 |  |
| `custodian_phone` | 117 | no | 4142155025 \| 2025480880 \| 2243210727 | 0.133333 |  |
| `custodian_state` | 117 | no | WI \| VA \| IL | 0.025000 |  |
| `custodian_street_1` | 117 | no | 10154 W KIEHNAU AVE \| PO BOX 26141 \| PO BOX 14047 | 0.025000 |  |
| `custodian_street_2` | 117 | no | A \| #1300 \| SUITE 810 | 0.666667 |  |
| `custodian_zip` | 117 | no | 53224 \| 22313 \| 60614 | 0.025000 |  |
| `cycles` | 117 | no | [2024, 2026] \| [2026] \| [2022, 2024, 2026] | 0.000000 |  |
| `designation` | 117 | no | B \| P \| A | 0.000000 |  |
| `designation_full` | 117 | no | Lobbyist/Registrant PAC \| Principal campaign committee \| Authorized by a candidate | 0.000000 |  |
| `email` | 117 | no | NATHANEBILLIPSFORUSREP2026@GMAIL.COM;NATHANBILLIPSJR@GMAIL.COM \| CHRIS@ELECTIONCFO.COM;EVANS@CC.ELECTIONCFO.COM \| COMPLIANCE@KATZCOMPLIANCE.COM | 0.000000 |  |
| `fax` | 117 | no |  | 1.000000 |  |
| `filing_frequency` | 117 | no | Q \| T \| A | 0.000000 |  |
| `first_f1_date` | 117 | no | 2024-06-24 \| 2025-10-01 \| 2025-09-19 | 0.000000 |  |
| `first_file_date` | 117 | no | 2024-06-24 \| 2025-10-01 \| 2025-09-19 | 0.000000 |  |
| `form_type` | 117 | no | F1 | 0.000000 |  |
| `last_f1_date` | 117 | no | 2026-02-02 \| 2025-10-01 \| 2025-09-19 | 0.000000 |  |
| `last_file_date` | 117 | no | 2026-02-02 \| 2026-01-19 \| 2026-01-30 | 0.000000 |  |
| `leadership_pac` | 117 | no |  | 1.000000 |  |
| `lobbyist_registrant_pac` | 117 | no | G | 0.991667 |  |
| `name` | 117 | no | NATHAN E BILLIPS JR \| JEANNIE FOR ILLINOIS \| BOTSFORD FOR ILLINOIS | 0.000000 |  |
| `organization_type` | 117 | no |  | 1.000000 |  |
| `organization_type_full` | 117 | no |  | 1.000000 |  |
| `party` | 117 | no | IND \| REP \| DEM | 0.000000 |  |
| `party_full` | 117 | no | INDEPENDENT \| REPUBLICAN PARTY \| DEMOCRATIC PARTY | 0.000000 |  |
| `party_type` | 117 | no |  | 1.000000 |  |
| `party_type_full` | 117 | no |  | 1.000000 |  |
| `sponsor_candidate_ids` | 117 | no |  | 1.000000 |  |
| `state` | 117 | no | WI \| VA \| IL | 0.000000 |  |
| `state_full` | 117 | no | Wisconsin \| Virginia \| Illinois | 0.000000 |  |
| `street_1` | 117 | no | 10154 W KIEHNAU AVE \| PO BOX 26141 \| PO BOX 14047 | 0.000000 |  |
| `street_2` | 117 | no | A \| SUITE 1300 \| SUITE 810 | 0.725000 |  |
| `treasurer_city` | 117 | no | MILWAUKEE \| ALEXANDRIA \| CHICAGO | 0.025000 |  |
| `treasurer_name` | 117 | no | SHELTON, BONNIE LASHAWN MRS \| MARSTON, CHRIS \| BOTSFORD, STEVE JR. | 0.000000 |  |
| `treasurer_name_1` | 117 | no | BONNIE \| CHRIS \| STEVE | 0.025000 |  |
| `treasurer_name_2` | 117 | no | SHELTON \| MARSTON \| BOTSFORD | 0.025000 |  |
| `treasurer_name_middle` | 117 | no | LASHAWN \| JAMES \| P. | 0.850000 |  |
| `treasurer_name_prefix` | 117 | no | MRS \| MS \| MRS. | 0.950000 |  |
| `treasurer_name_suffix` | 117 | no | JR. \| II | 0.983333 |  |
| `treasurer_name_title` | 117 | no | DESIGNATED AGENT \| TREASURER \| TEMP. | 0.300000 |  |
| `treasurer_phone` | 117 | no | 4142155025 \| 2025480880 \| 2243210727 | 0.058333 |  |
| `treasurer_state` | 117 | no | WI \| VA \| IL | 0.025000 |  |
| `treasurer_street_1` | 117 | no | 10154 W KIEHNAU AVE \| PO BOX 26141 \| PO BOX 14047 | 0.025000 |  |
| `treasurer_street_2` | 117 | no | A \| #1300 \| SUITE 810 | 0.700000 |  |
| `treasurer_zip` | 117 | no | 53224 \| 22313 \| 60614 | 0.025000 |  |
| `website` | 117 | no | HTTPS://WWW.CHANGE.ORG/NATHANEBILLIPSUSREP \| HTTPS://WWW.JEANNIEEVANS.COM/ \| DONTRACYFORIL.COM | 0.233333 |  |
| `zip` | 117 | no | 53224 \| 22313 \| 60614 | 0.000000 |  |

### fec_api_candidate_totals_json

- raw files: `raw_extractions:fec_api:candidate_S8IL00215_totals:e82fe60fd22067815b5ee870a7299a340578957a`, `raw_extractions:fec_api:candidate_S6IL00656_totals:06d8956f583907f17a9e1f6b7203c901f04260ff`, `raw_extractions:fec_api:candidate_S6IL00672_totals:065c619ab702aae9c9486957b522f741e2cd6ff1`, `raw_extractions:fec_api:candidate_S6IL00649_totals:5c1a52ec69f4fff24604e41865f022afb79002b6`, `raw_extractions:fec_api:candidate_S6IL00623_totals:561db722620df321be721162f8061666de534732`, `raw_extractions:fec_api:candidate_S6IL00615_totals:2ba91570f44b39609526d4fa97411c4fafc6d35e`, `raw_extractions:fec_api:candidate_S6IL00607_totals:a0225a52a39856ed5e16e0d0f02ef850c517a233`, `raw_extractions:fec_api:candidate_S6IL00581_totals:c7611298fb52b4b64e9b1a5fa2f8174a5b3c4771`, `... (+116 more)`
- union_of_columns_count: 48
- intersection_of_columns_count: 0

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `all_other_loans` | 95 | no | 0.0 \| 2500.0 \| 2000.0 | 0.000000 |  |
| `candidate_contribution` | 95 | no | 353925.31 \| 121706.39 \| 0.0 | 0.000000 |  |
| `candidate_election_year` | 95 | no | 2026 | 0.010526 |  |
| `candidate_id` | 95 | no | S6IL00656 \| S6IL00672 \| S6IL00649 | 0.000000 |  |
| `contribution_refunds` | 95 | no | 0.0 \| 520.51 \| 31091.5 | 0.000000 |  |
| `contributions` | 95 | no | 359170.31 \| 203861.39 \| 21976.0 | 0.000000 |  |
| `coverage_end_date` | 95 | no | 2025-12-31T00:00:00 \| 2025-09-30T00:00:00 \| 2025-04-16T00:00:00 | 0.000000 |  |
| `coverage_start_date` | 95 | no | 2025-07-01T00:00:00 \| 2025-10-01T00:00:00 \| 2025-04-01T00:00:00 | 0.000000 |  |
| `cycle` | 95 | no | 2026 | 0.000000 |  |
| `disbursements` | 95 | no | 230990.53 \| 212267.81 \| 14778.34 | 0.000000 |  |
| `election_full` | 95 | no | False | 0.000000 |  |
| `exempt_legal_accounting_disbursement` | 95 | no | 0.0 | 0.000000 |  |
| `federal_funds` | 95 | no | 0.0 | 0.000000 |  |
| `fundraising_disbursements` | 95 | no | 0.0 | 0.000000 |  |
| `individual_contributions` | 95 | no | 5245.0 \| 82155.0 \| 21976.0 | 0.000000 |  |
| `individual_itemized_contributions` | 95 | no | 4250.0 \| 81500.0 \| 20232.0 | 0.000000 |  |
| `individual_unitemized_contributions` | 95 | no | 995.0 \| 655.0 \| 1744.0 | 0.000000 |  |
| `last_beginning_image_number` | 95 | no | 202601309794556075 \| 202601199794075787 \| 202601319795637048 | 0.000000 |  |
| `last_cash_on_hand_end_period` | 95 | no | 128180.29 \| 298594.09 \| 7197.66 | 0.000000 |  |
| `last_debts_owed_by_committee` | 95 | no | 0.0 \| 300000.0 \| 46500.0 | 0.000000 |  |
| `last_debts_owed_to_committee` | 95 | no | 0.0 | 0.000000 |  |
| `last_net_contributions` | 95 | no | 257378.76 \| 203861.39 \| 16875.0 | 0.000000 |  |
| `last_net_operating_expenditures` | 95 | no | 129198.98 \| 205267.3 \| 12659.01 | 0.000000 |  |
| `last_report_type_full` | 95 | no | YEAR-END \| OCTOBER QUARTERLY \| TERMINATION REPORT | 0.000000 |  |
| `last_report_year` | 95 | no | 2025 | 0.000000 |  |
| `loan_repayments` | 95 | no | 0.0 \| 4547.62 \| 2000.0 | 0.000000 |  |
| `loan_repayments_candidate_loans` | 95 | no | 0.0 \| 2547.62 \| 2000.0 | 0.000000 |  |
| `loan_repayments_other_loans` | 95 | no | 0.0 \| 2000.0 | 0.000000 |  |
| `loans` | 95 | no | 0.0 \| 300000.0 \| 46500.0 | 0.000000 |  |
| `loans_made_by_candidate` | 95 | no | 0.0 \| 300000.0 \| 46500.0 | 0.000000 |  |
| `net_contributions` | 95 | no | 359170.31 \| 203861.39 \| 21976.0 | 0.000000 |  |
| `net_operating_expenditures` | 95 | no | 230990.53 \| 205267.3 \| 14778.34 | 0.000000 |  |
| `offsets_to_fundraising_expenditures` | 95 | no | 0.0 | 0.000000 |  |
| `offsets_to_legal_accounting` | 95 | no | 0.0 | 0.000000 |  |
| `offsets_to_operating_expenditures` | 95 | no | 0.0 \| 7000.51 \| 500.0 | 0.000000 |  |
| `operating_expenditures` | 95 | no | 230990.53 \| 212267.81 \| 14778.34 | 0.000000 |  |
| `other_disbursements` | 95 | no | 0.0 \| 1553.67 \| 6332.68 | 0.000000 |  |
| `other_political_committee_contributions` | 95 | no | 0.0 \| 150.0 \| 120.0 | 0.000000 |  |
| `other_receipts` | 95 | no | 0.51 \| 0.0 \| 19288.35 | 0.000000 |  |
| `political_party_committee_contributions` | 95 | no | 0.0 \| 250.0 \| 500.0 | 0.000000 |  |
| `receipts` | 95 | no | 359170.82 \| 510861.9 \| 21976.0 | 0.000000 |  |
| `refunded_individual_contributions` | 95 | no | 0.0 \| 520.51 \| 31091.5 | 0.000000 |  |
| `refunded_other_political_committee_contributions` | 95 | no | 0.0 \| 2000.0 \| 8500.0 | 0.000000 |  |
| `refunded_political_party_committee_contributions` | 95 | no | 0.0 | 0.000000 |  |
| `total_offsets_to_operating_expenditures` | 95 | no | 0.0 | 0.000000 |  |
| `transaction_coverage_date` | 95 | no | 2025-09-30T00:00:00 \| 2025-12-31T00:00:00 \| 2025-04-16T00:00:00 | 0.031579 |  |
| `transfers_from_other_authorized_committee` | 95 | no | 0.0 \| 4205.0 \| 19285171.21 | 0.000000 |  |
| `transfers_to_other_authorized_committee` | 95 | no | 0.0 \| 1000.0 \| 2060.0 | 0.000000 |  |

### openbook_contracts_search_json

- raw files: `raw_extractions:openbook_contracts_search:BLUEGRASS FIRE PROTECTION DIST:contracts:26`, `raw_extractions:openbook_contracts_search:BLUEDAG LLC:contracts:26`, `raw_extractions:openbook_contracts_search:BLUECAT NETWORKS:contracts:26`, `raw_extractions:openbook_contracts_search:BLUEBOLT, INC:contracts:26`, `raw_extractions:openbook_contracts_search:BLUEAPPLE HEALTH LLC:contracts:26`, `raw_extractions:openbook_contracts_search:BLUEAPPLE HEALTH:contracts:26`, `raw_extractions:openbook_contracts_search:BLUE1647NFP:contracts:26`, `raw_extractions:openbook_contracts_search:BLUE TOWER SOLUTIONS INC:contracts:26`, `... (+48 more)`
- union_of_columns_count: 8
- intersection_of_columns_count: 8

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `agency_code` | 56 | yes | 592 \| 340 \| 108 | 0.000000 |  |
| `agency_name` | 56 | yes | OFFICE OF THE STATE FIRE MARSH \| ATTORNEY GENERAL \| LEGISLATIVE INFORMATION SYSTEM | 0.000000 |  |
| `award_amount` | 56 | yes | 24148.0 \| 39142.22 \| 61142.23 | 0.000000 |  |
| `contract_number` | 56 | yes | 40000014118 \| 584BLUE1148 \| 484BLUE1148 | 0.000000 |  |
| `detail_url` | 56 | yes | https://office.illinoiscomptroller.gov/get/Enhanced_Ledger_By_vendor/Vendor_Warrant_Listing_Dates_Amounts.cfm?Vendor_Name=BLUEDAG%20LLC%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20&Agency=340&Contract_Number=584BLUE1148&Fiscal_Year=2025 \| https://office.illinoiscomptroller.gov/get/Enhanced_Ledger_By_vendor/Vendor_Warrant_Listing_Dates_Amounts.cfm?Vendor_Name=BLUEDAG%20LLC%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20&Agency=340&Contract_Number=484BLUE1148&Fiscal_Year=2024 \| https://office.illinoiscomptroller.gov/get/Enhanced_Ledger_By_vendor/Vendor_Warrant_Listing_Dates_Amounts.cfm?Vendor_Name=BLUEAPPLE%20HEALTH%20LLC%20%20%20%20%20%20%20%20%20%20%20%20&Agency=557&Contract_Number=64100199896&Fiscal_Year=2026 | 0.789050 |  |
| `fiscal_year` | 56 | yes | 2014 \| 2025 \| 2024 | 0.000000 |  |
| `row_hash` | 56 | yes | d33a8a14c1b73f5305c7ff919e92e2d0ce59bf8d \| d4076195b161481bd851586f72d8418570a8928a \| f49ee05f68e8813b91765b44c5a5549361a8769f | 0.000000 |  |
| `vendor_label` | 56 | yes | BLUEGRASS FIRE PROTECTION DIST \| BLUEDAG LLC \| BLUECAT NETWORKS | 0.000000 |  |

### openbook_contributions_tab_json

- raw files: `raw_extractions:openbook_contributions_tab:BLUEGRASS FIRE PROTECTION DIST:contributions:26`, `raw_extractions:openbook_contributions_tab:BLUEDAG LLC:contributions:26`, `raw_extractions:openbook_contributions_tab:BLUECAT NETWORKS:contributions:26`, `raw_extractions:openbook_contributions_tab:BLUEBOLT, INC:contributions:26`, `raw_extractions:openbook_contributions_tab:BLUEAPPLE HEALTH LLC:contributions:26`, `raw_extractions:openbook_contributions_tab:BLUEAPPLE HEALTH:contributions:26`, `raw_extractions:openbook_contributions_tab:BLUE1647NFP:contributions:26`, `raw_extractions:openbook_contributions_tab:BLUE TOWER SOLUTIONS INC:contributions:26`, `... (+48 more)`
- union_of_columns_count: 7
- intersection_of_columns_count: 7

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `amount` | 56 | yes |  | 1.000000 |  |
| `contribution_date` | 56 | yes | DATE | 0.000000 |  |
| `contributor_first_name` | 56 | yes |  | 1.000000 |  |
| `contributor_name` | 56 | yes | &nbsp; | 0.000000 |  |
| `employer` | 56 | yes | CONTRIBUTEDBY | 0.000000 |  |
| `recipient_name` | 56 | yes | AMOUNT | 0.000000 |  |
| `row_hash` | 56 | yes | 87db55b7bb2939c908bcc42d17278522fc26c16f \| a636191ad2466d1728e739fc5f534ba6f56040bc \| 8e37713c78df6f3820cb6015b1a72fbf82a34909 | 0.000000 |  |

### openbook_employees_tab_json

- blocked: yes (No matching raw_extractions rows for source_type pattern.)
- notes: Parsed employees-tab payload snapshots persisted in raw_extractions (not full HTML).

### openbook_contract_detail_json

- raw files: `raw_extractions:openbook_contract_detail:BLUEDAG LLC:detail:484BLUE1148:2024:26`, `raw_extractions:openbook_contract_detail:BLUEDAG LLC:detail:584BLUE1148:2025:26`, `raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH LLC:detail:54100199896:2025:26`, `raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH LLC:detail:64100199896:2026:26`, `raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH:detail:34100111021:2023:26`, `raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH:detail:34100113294:2023:26`, `raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH:detail:44100111021:2024:26`, `raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH:detail:44100113294:2024:26`, `... (+72 more)`
- union_of_columns_count: 6
- intersection_of_columns_count: 6

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `contract_number` | 80 | yes | 484BLUE1148 \| 584BLUE1148 \| 54100199896 | 0.000000 |  |
| `detail_url` | 80 | yes | https://office.illinoiscomptroller.gov/get/Enhanced_Ledger_By_vendor/Vendor_Warrant_Listing_Dates_Amounts.cfm?Vendor_Name=BLUEDAG%20LLC%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20&Agency=340&Contract_Number=484BLUE1148&Fiscal_Year=2024 \| https://office.illinoiscomptroller.gov/get/Enhanced_Ledger_By_vendor/Vendor_Warrant_Listing_Dates_Amounts.cfm?Vendor_Name=BLUEDAG%20LLC%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20&Agency=340&Contract_Number=584BLUE1148&Fiscal_Year=2025 \| https://office.illinoiscomptroller.gov/get/Enhanced_Ledger_By_vendor/Vendor_Warrant_Listing_Dates_Amounts.cfm?Vendor_Name=BLUEAPPLE%20HEALTH%20LLC%20%20%20%20%20%20%20%20%20%20%20%20&Agency=557&Contract_Number=54100199896&Fiscal_Year=2025 | 0.000000 |  |
| `fiscal_year` | 80 | yes | 2024 \| 2025 \| 2026 | 0.000000 |  |
| `vendor_key` | 80 | yes | BLUEDAG LLC \| BLUEAPPLE HEALTH LLC \| BLUEAPPLE HEALTH | 0.000000 |  |
| `warrant_count` | 80 | yes | 0 | 0.000000 |  |
| `warrants` | 80 | yes | [] | 0.000000 |  |

### chicago_contracts_socrata_json

- raw files: `https://data.cityofchicago.org/resource/rsxa-ify5.json?%24limit=5000&%24order=%3Aid`
- union_of_columns_count: 19
- intersection_of_columns_count: 19

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `purchase_order_description` | 1 | yes | DEMOLITION \| JANITORIAL SUPPLIES \| MASTER AGREEMENT FOR DEMOLITION SERVICES | 0.009600 |  |
| `purchase_order_contract_number` | 1 | yes | E011442 \| T27500 \| C02163 | 0.000000 |  |
| `revision_number` | 1 | yes | 0 \| 1 \| 7 | 0.000000 |  |
| `specification_number` | 1 | yes | E968020030 \| B74851001 \| B89683203 | 0.000200 |  |
| `contract_type` | 1 | yes | CONSTRUCTION-GENERAL \| WORK SERVICES-SMALL ORDERS \| COMMODITIES-SMALL ORDERS | 0.005400 |  |
| `start_date` | 1 | yes | 1997-12-01T00:00:00.000 \| 2014-03-07T00:00:00.000 \| 2012-11-13T00:00:00.000 | 0.536400 |  |
| `end_date` | 1 | yes | 1999-11-30T00:00:00.000 \| 2017-03-06T00:00:00.000 \| 2014-11-12T00:00:00.000 | 0.580800 |  |
| `approval_date` | 1 | yes | 2014-09-05T00:00:00.000 \| 2014-08-28T00:00:00.000 \| 2014-08-25T00:00:00.000 | 0.002400 |  |
| `department` | 1 | yes | DEPARTMENT OF BUILDINGS \| CHICAGO DEPARTMENT OF TRANSPORTATION \| CHICAGO PUBLIC LIBRARY | 0.130400 |  |
| `vendor_name` | 1 | yes | MIDWEST WRECKING COMPANY 01 \| CHICAGO UNITED INDUSTRIES, LIMITED \| DEMOLITION & DEVELOPMENT, LIMITED. | 0.000000 |  |
| `vendor_id` | 1 | yes | 14878414T \| 22085024V \| 22414299V | 0.000000 |  |
| `address_1` | 1 | yes | 1950 W HUBBARD ST \| 53 W JACKSON BLVD # 1450 \| P.0. BOX 10263 | 0.000000 |  |
| `address_2` | 1 | yes | 3228 S WOOD ST \| # 4 \| 7950 JOLIET RD STE 200 | 0.871200 |  |
| `city` | 1 | yes | CHICAGO \| BURLINGTON \| GLENVIEW | 0.000000 |  |
| `state` | 1 | yes | IL \| MA \| NE | 0.000000 |  |
| `zip` | 1 | yes | 60622 \| 60604-3806 \| 60610 | 0.009200 |  |
| `award_amount` | 1 | yes | 17500 \| 0 \| 7000 | 0.000000 |  |
| `procurement_type` | 1 | yes | BID \| MASTER AGREEMENT \| RFQ | 0.444600 |  |
| `contract_pdf` | 1 | yes |  | 1.000000 |  |

### chicago_payments_socrata_json

- raw files: `https://data.cityofchicago.org/resource/s4vu-giwb.json?%24limit=5000&%24order=%3Aid`
- union_of_columns_count: 6
- intersection_of_columns_count: 6

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `voucher_number` | 1 | yes | PV81248102104 \| CVIP244117202 \| PV27252766021 | 0.318000 |  |
| `amount` | 1 | yes | 175 \| 69.85 \| 38554.81 | 0.000000 |  |
| `check_date` | 1 | yes | 12/18/2024 \| 03/27/2025 \| 2021 | 0.000000 |  |
| `department_name` | 1 | yes | DEPARTMENT OF WATER MANAGEMENT \| CHICAGO DEPARTMENT OF TRANSPORTATION \| DEPT OF ASSETS INFORMATION AND SERVICES | 0.660000 |  |
| `contract_number` | 1 | yes | DV \| 241821 \| 124392 | 0.000000 |  |
| `vendor_name` | 1 | yes | THOMAS HEITZMAN \| CHRISTIAN COMMUNITY HEALTH CENTER \| TRAVELERS & IMMIGRANTS AID'S HEARTLAND ALLIANCE FOR HUMAN NEEDS & HUMAN RIGHTS | 0.000000 |  |

### chicago_lobbyist_contributions_socrata_json

- raw files: `https://data.cityofchicago.org/resource/p9p7-vfqc.json?%24limit=5000&%24order=%3Aid`
- union_of_columns_count: 10
- intersection_of_columns_count: 10

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `contribution_id` | 1 | yes | 997687494 \| 1772781085 \| 3074589131 | 0.000000 |  |
| `period_start` | 1 | yes | 2016-01-01T00:00:00.000 \| 2019-10-01T00:00:00.000 \| 2016-04-01T00:00:00.000 | 0.000000 |  |
| `period_end` | 1 | yes | 2016-03-31T00:00:00.000 \| 2019-12-31T00:00:00.000 \| 2016-06-30T00:00:00.000 | 0.000000 |  |
| `contribution_date` | 1 | yes | 2016-01-28T00:00:00.000 \| 2019-11-07T00:00:00.000 \| 2016-05-23T00:00:00.000 | 0.000000 |  |
| `recipient` | 1 | yes | DEMOCRATIC PARTY OF THE 49TH WARD \| CHRIS TALIAFERRO \| ALD HOPKINS | 0.000000 |  |
| `amount` | 1 | yes | 1500 \| 250 \| 500 | 0.000000 |  |
| `lobbyist_id` | 1 | yes | 4254 \| 18181 \| 3912 | 0.000000 |  |
| `lobbyist_first_name` | 1 | yes | ARNOLD \| JOHN \| TERRY | 0.000000 |  |
| `lobbyist_last_name` | 1 | yes | HARRIS \| DALEY \| GABINSKI | 0.000000 |  |
| `created_date` | 1 | yes | 2016-04-19T00:00:00.000 \| 2020-01-20T00:00:00.000 \| 2017-01-30T00:00:00.000 | 0.000000 |  |

### chicago_lobbying_activity_socrata_json

- raw files: `https://data.cityofchicago.org/resource/pahz-egmi.json?%24limit=5000&%24order=%3Aid`
- union_of_columns_count: 13
- intersection_of_columns_count: 13

| raw_column_name | present_in_files_count | present_in_all_files? | example_values | null_rate_estimate | notes |
|---|---:|---|---|---:|---|
| `lobbying_activity_id` | 1 | yes | 4024574064 \| 334762988 \| 3913494580 | 0.000000 |  |
| `period_start` | 1 | yes | 2014-07-01T00:00:00.000 \| 2019-10-01T00:00:00.000 \| 2016-10-01T00:00:00.000 | 0.000000 |  |
| `period_end` | 1 | yes | 2014-09-30T00:00:00.000 \| 2019-12-31T00:00:00.000 \| 2016-12-31T00:00:00.000 | 0.000000 |  |
| `action` | 1 | yes | LEGISLATIVE \| BOTH (ADMINISTRATIVE AND LEGISLATIVE) \| ADMINISTRATIVE | 0.000000 |  |
| `action_sought` | 1 | yes | AMENDMENT TO PLANNED DEVELOPMENT \| DISCUSSED FY 2020 BUDGET \| CONTRACT APPROVAL | 0.000000 |  |
| `department` | 1 | yes | TRANSPORTATION \| MAYORS OFFICE \| PROCUREMENT SERVICES | 0.000000 |  |
| `client_id` | 1 | yes | 2201517748 \| 83073491 \| 2367121887 | 0.000000 |  |
| `client_name` | 1 | yes | FRIEDMAN PROPERTIES \| ILLINOIS RESTAURANT ASSOCIATION \| WESTFIELD GROUP | 0.000000 |  |
| `lobbyist_id` | 1 | yes | 6842 \| 5201 \| 3744 | 0.000000 |  |
| `lobbyist_first_name` | 1 | yes | JOHN \| SAM \| TIMOTHY | 0.000000 |  |
| `lobbyist_middle_initial` | 1 | yes | J \| A \| S | 0.396200 |  |
| `lobbyist_last_name` | 1 | yes | GEORGE \| TOIA \| DART | 0.000000 |  |
| `created_date` | 1 | yes | 2015-01-26T00:00:00.000 \| 2020-01-16T00:00:00.000 \| 2017-01-04T00:00:00.000 | 0.000000 |  |

### Schema drift

- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:6000549e9a2a8a84332cd8488aebe730827c7b12`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:357806cc5854b7f0668517236d284307aef8c383`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:55c0709b8ff9aa6fb36e7158e97063277069eb4e`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:13e594818d4eff7d4fee797e356bfcc4e32c194e`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:84a8075da95049f5e810107fceca27b1fc0a347d`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:62365ef556eaf703d285d1b86a7ba2cbc4028f8a`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:5f2c2dafdab2bfdc20666e873b32e86ad42e225e`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:1c7e831821656b64486c75edea56b3b7d9678efb`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:d23a29e899e838e75233303a1613cfe91e60ff63`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:7a8e588bdba82399b49ebfab6562ffd92e0d71b7`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:681bcdded54857f5c741f1d8cf2b342b0fab474c`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:73b932122245860da87559530c17adc440ddc835`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:180632a3d7b1013ae8655da42be331814e6bfb6f`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:4c1affc121e7d9d9c370426d1d43e8fe89788209`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:fa2304d29d828db42e848aacf1db804c0369a2cf`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:0a7999a01219dd3c8a0838e9d2c92e5307b60cec`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:6f42578d1e5bed07368d039e16bc7c27f1b4d4fe`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:181f6bbdea491de5734e89636cbc1c641d70ff64`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:f72c6ade974de8b1bc298e2f0ea39db0e7c6264e`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:4e7d25650269b3bee8e1c45a47e679ae070c1690`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:94d3d68556d00c84dbaf3f84c4cea3702e910263`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:ecccf71567ee2369f738b17fd706d13a11416ddb`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:ffeb15f7dc954d3c19530b63cc198d2947d17402`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:ffe124e38e9765fa1df056a1f483ce0e60e632c4`
- fec_api_schedule_a_json: `raw_extractions:fec_api:schedules_schedule_a:bdcd16495383d0481790b763844bbeb37d22a00c`
- ... (+1178 additional drift entries)

## Column lineage map

| feed_name | RAW | STAGING | CORE/DB | API/EXPORT/UI |
|---|---|---|---|---|
| chicago_contracts_socrata_json | `https://data.cityofchicago.org/resource/rsxa-ify5.json?%24limit=5000&%24order=%3Aid` | `` | `chicago_contracts_raw.approval_date; chicago_contracts_raw.award_amount; chicago_contracts_raw.city; chicago_contracts_raw.contract_pdf; ... (+13 more)` | `` |
| chicago_lobbying_activity_socrata_json | `https://data.cityofchicago.org/resource/pahz-egmi.json?%24limit=5000&%24order=%3Aid` | `` | `chicago_lobbying_activity_raw.action; chicago_lobbying_activity_raw.action_sought; chicago_lobbying_activity_raw.client_id; chicago_lobbying_activity_raw.client_name; ... (+7 more)` | `` |
| chicago_lobbyist_contributions_socrata_json | `https://data.cityofchicago.org/resource/p9p7-vfqc.json?%24limit=5000&%24order=%3Aid` | `` | `chicago_lobbyist_contributions_raw.amount; chicago_lobbyist_contributions_raw.contribution_date; chicago_lobbyist_contributions_raw.contribution_id; chicago_lobbyist_contributions_raw.lobbyist_first_name; ... (+5 more)` | `` |
| chicago_payments_socrata_json | `https://data.cityofchicago.org/resource/s4vu-giwb.json?%24limit=5000&%24order=%3Aid` | `` | `chicago_payments_raw.amount; chicago_payments_raw.check_date_raw; chicago_payments_raw.contract_number; chicago_payments_raw.department_name; ... (+2 more)` | `` |
| fec_api_candidate_committees_json | `... (+112 more); raw_extractions:fec_api:candidate_H4IL05130_committees:0f42e322ba94ff66efbf35ae2380990b199393a7; raw_extractions:fec_api:candidate_H6IL07412_committees:51603c6f77622d4cc73fe0af0d7fa2ec782db2cb; raw_extractions:fec_api:candidate_H6IL07529_committees:bc9976d87eb18b5e6ce93fd95803a09c06f244ee; ... (+9 more)` | `raw_extractions.payload_json` | `fec_candidate_committees.*` | `database/federal_fec.py; webapp/routes/main.py` |
| fec_api_candidate_totals_json | `... (+112 more); raw_extractions:fec_api:candidate_S6IL00490_totals:dbc7e84a84aa07b5b84753a6a57f76f426e58926; raw_extractions:fec_api:candidate_S6IL00540_totals:7e2e61e9f96892e84ab61185845f2916d8889433; raw_extractions:fec_api:candidate_S6IL00557_totals:63f2eeba8dec2fd7c8ae89e55817d681c1112fd8; ... (+9 more)` | `raw_extractions.payload_json` | `fec_candidate_cycle_totals.*` | `database/federal_fec.py; scripts/newsroom_queries.py; webapp/routes/main.py` |
| fec_api_candidates_search_json | `raw_extractions:fec_api:candidates_search:30d222d46a0d98eb68dd9d5c7d1474fa0af310e7; raw_extractions:fec_api:candidates_search:8130a8140a97988703c765d0925d4697ca71f089; raw_extractions:fec_api:candidates_search:942e94d3a7c3608a14db2b0e394b2a0d7f744456; raw_extractions:fec_api:candidates_search:da87a8d4f8477505e504fe00ee27b5aaf88876af; ... (+1 more)` | `raw_extractions.payload_json` | `fec_candidate_match.*` | `database/federal_fec.py; scripts/newsroom_queries.py` |
| fec_api_schedule_a_json | `... (+1673 more); raw_extractions:fec_api:schedules_schedule_a:0dbf760a699f91678d9afe0d73835c1ed5efb6b4; raw_extractions:fec_api:schedules_schedule_a:2d89850b7d91c9f74db2196f1a07860219e324cf; raw_extractions:fec_api:schedules_schedule_a:440608d0054e46e524ef222748e33c225959c381; ... (+9 more)` | `raw_extractions.payload_json` | `fec_schedule_a_contributions.*` | `database/analytics.py; database/federal_fec.py; webapp/routes/main.py` |
| fec_api_schedule_b_json | `... (+309 more); raw_extractions:fec_api:schedules_schedule_b:109926a3bf17c8498ee0728677090ca4fd66b7be; raw_extractions:fec_api:schedules_schedule_b:3acfb2eaa92e3651dc3316cd2dd8c80a2cb3558f; raw_extractions:fec_api:schedules_schedule_b:67c63250d1f52718fb9346b37a61c7bcdd6c0d33; ... (+9 more)` | `raw_extractions.payload_json` | `fec_schedule_b_disbursements.*` | `database/federal_fec.py; webapp/routes/federal_finance.py; webapp/routes/main.py` |
| fec_api_schedule_e_json | `... (+181 more); raw_extractions:fec_api:schedules_schedule_e:03174e58bcff24077bac640eeb9b44fbed6a37d0; raw_extractions:fec_api:schedules_schedule_e:1f7a39266fd8cd1c906b956cb971e4ec6c379c3d; raw_extractions:fec_api:schedules_schedule_e:25de5e68561efe42a454cc88af0b9b0ec8f6396d; ... (+9 more)` | `raw_extractions.payload_json` | `fec_schedule_e_independent_expenditures.*` | `database/federal_fec.py; scripts/newsroom_queries.py; webapp/routes/federal_finance.py; webapp/routes/main.py` |
| ilsos_lobbying_active_clients_csv | `Bulk_download/ILSOS_Lobbying_activeandclients/Active_Lobbying_Entities_and_Their_Clients_20260213.csv` | `` | `lobbying_clients.client_id; lobbying_clients.client_name; lobbying_entities.entity_id; lobbying_entities.entity_name; ... (+1 more)` | `database/cross_matching.py; scraper/openbook_scraper.py; webapp/routes/lobbying.py` |
| ilsos_lobbying_daily_csv | `Bulk_download/Lobbyist_Entity_Client_Data_Daily_20260214.csv` | `` | `lobbying_clients.address_1; lobbying_clients.address_2; lobbying_clients.city; lobbying_clients.client_id; ... (+24 more)` | `database/cross_matching.py; docs/case_studies/ameren_lobbying_relationships.md; scraper/openbook_scraper.py; webapp/routes/lobbying.py` |
| irs527_full_data_record_1_org_registration | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `irs527_organizations.address_1; irs527_organizations.address_2; irs527_organizations.business_address_1; irs527_organizations.business_address_2; ... (+33 more)` | `database/cross_matching.py; webapp/routes/irs527.py` |
| irs527_full_data_record_2_periodic_report | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `irs527_reports.business_address_1; irs527_reports.business_address_2; irs527_reports.business_city; irs527_reports.business_state; ... (+39 more)` | `scripts/build_triple_pipeline.py; webapp/routes/irs527.py` |
| irs527_full_data_record_A_contribution | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `irs527_contributions.amount; irs527_contributions.city; irs527_contributions.contributor_address; irs527_contributions.contributor_address_2; ... (+10 more)` | `scripts/build_triple_pipeline.py; webapp/routes/irs527.py` |
| irs527_full_data_record_B_expenditure | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `irs527_expenditures.amount; irs527_expenditures.city; irs527_expenditures.date; irs527_expenditures.ein; ... (+11 more)` | `database/cross_matching.py; webapp/routes/irs527.py` |
| irs527_full_data_record_D_director | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `irs527_directors.address_1; irs527_directors.address_2; irs527_directors.city; irs527_directors.ein; ... (+7 more)` | `database/cross_matching.py; webapp/routes/irs527.py` |
| irs527_full_data_record_E_election_authority | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `irs527_election_authority.election_authority_id; irs527_election_authority.form_id; irs527_election_authority.state` | `database/irs527_loader.py` |
| irs527_full_data_record_H_header | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `` | `` |
| irs527_full_data_record_R_related_org | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `irs527_related_orgs.address_1; irs527_related_orgs.address_2; irs527_related_orgs.city; irs527_related_orgs.ein; ... (+7 more)` | `webapp/routes/irs527.py` |
| irs527_full_data_record_UNKNOWN | `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt` | `` | `` | `` |
| isbe_candidates_tsv | `Bulk_download/Candidates.txt; Bulk_download/candidates_639060037834639303.txt` | `` | `bulk_candidates_clean.address_line_1; bulk_candidates_clean.address_line_2; bulk_candidates_clean.candidate_id; bulk_candidates_clean.city; ... (+10 more)` | `database/cross_matching.py; webapp/routes/committees.py; webapp/routes/lobbying.py` |
| isbe_canelections_tsv | `Bulk_download/CanElections.txt` | `` | `` | `` |
| isbe_cmte_candidate_links_tsv | `Bulk_download/CmteCandidateLinks.txt; Bulk_download/cmtecandidatelinks_639060047756941723.txt` | `` | `bulk_cmte_candidate_links_clean.candidate_id; bulk_cmte_candidate_links_clean.committee_id_sbe; bulk_cmte_candidate_links_clean.link_record_id` | `database/analytics.py; webapp/routes/committees.py; webapp/routes/lobbying.py` |
| isbe_cmte_officer_links_tsv | `Bulk_download/CmteOfficerLinks.txt` | `` | `` | `` |
| isbe_committees_tsv | `Bulk_download/Committees.txt; Bulk_download/committees_639060038163077225.txt` | `` | `bulk_committees_clean.address_line_1; bulk_committees_clean.address_line_2; bulk_committees_clean.address_line_3; bulk_committees_clean.candidate_support_or_oppose; ... (+23 more)` | `database/analytics.py; webapp/routes/committees.py; webapp/routes/lobbying.py` |
| isbe_d2totals_tsv | `Bulk_download/D2Totals.txt; Bulk_download/d2totals_639060039113297425.txt` | `` | `bulk_d2_totals_clean.beginning_funds_available; bulk_d2_totals_clean.committee_id_sbe; bulk_d2_totals_clean.d2_totals_record_id; bulk_d2_totals_clean.debts_obligations_itemized; ... (+27 more)` | `database/analytics.py; scripts/build_triple_pipeline.py` |
| isbe_expenditures_tsv | `Bulk_download/Expenditures.txt; Bulk_download/expenditures_639065716188368118.txt` | `` | `bulk_expenditures_clean.address_line_1; bulk_expenditures_clean.address_line_2; bulk_expenditures_clean.aggregate_amount; bulk_expenditures_clean.amount; ... (+19 more)` | `database/analytics.py; scraper/openbook_scraper.py; webapp/routes/committees.py` |
| isbe_filed_docs_tsv | `Bulk_download/FiledDocs.txt` | `` | `` | `` |
| isbe_investments_tsv | `Bulk_download/Investments.txt` | `` | `` | `` |
| isbe_officers_tsv | `Bulk_download/Officers.txt` | `` | `` | `` |
| isbe_prev_officers_tsv | `Bulk_download/PrevOfficers.txt` | `` | `` | `` |
| isbe_receipts_tsv | `Bulk_download/Receipts.txt; Bulk_download/receipts_639060048226403871.txt` | `` | `bulk_receipts_clean.address_line_1; bulk_receipts_clean.address_line_2; bulk_receipts_clean.aggregate_amount; bulk_receipts_clean.amount; ... (+25 more)` | `database/analytics.py; webapp/routes/committees.py; webapp/routes/lobbying.py` |
| openbook_contract_detail_json | `... (+68 more); raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH LLC:detail:54100199896:2025:26; raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH LLC:detail:64100199896:2026:26; raw_extractions:openbook_contract_detail:BLUEAPPLE HEALTH:detail:34100111021:2023:26; ... (+9 more)` | `raw_extractions.payload_json` | `` | `database/federal_fec.py; scraper/openbook_scraper.py` |
| openbook_contracts_search_json | `... (+44 more); raw_extractions:openbook_contracts_search:BLUE SKY MARKETING GROUP LTD:contracts:25; raw_extractions:openbook_contracts_search:BLUE SKY NETWORK:contracts:25; raw_extractions:openbook_contracts_search:BLUE SKY VINEYARD:contracts:26; ... (+9 more)` | `` | `openbook_contracts_raw.agency_code; openbook_contracts_raw.agency_name; openbook_contracts_raw.award_amount; openbook_contracts_raw.contract_number; ... (+4 more)` | `webapp/routes/main.py; webapp/routes/openbook.py` |
| openbook_contributions_tab_json | `... (+44 more); raw_extractions:openbook_contributions_tab:BLUE SKY MARKETING GROUP LTD:contributions:25; raw_extractions:openbook_contributions_tab:BLUE SKY NETWORK:contributions:25; raw_extractions:openbook_contributions_tab:BLUE SKY VINEYARD:contributions:26; ... (+9 more)` | `` | `openbook_contributions_raw.amount; openbook_contributions_raw.contribution_date; openbook_contributions_raw.contributor_first_name; openbook_contributions_raw.contributor_name; ... (+3 more)` | `webapp/routes/openbook.py` |

## 2) Per-feed Raw -> Stored -> Used tables

### chicago_contracts_socrata_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `purchase_order_description` | yes | `` | `chicago_contracts_raw.purchase_order_description` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `purchase_order_contract_number` | yes | `` | `chicago_contracts_raw.purchase_order_contract_number` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `revision_number` | yes | `` | `chicago_contracts_raw.revision_number` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `specification_number` | yes | `` | `chicago_contracts_raw.specification_number` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `contract_type` | yes | `` | `chicago_contracts_raw.contract_type` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `start_date` | yes | `` | `chicago_contracts_raw.start_date` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `end_date` | yes | `` | `chicago_contracts_raw.end_date` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `approval_date` | yes | `` | `chicago_contracts_raw.approval_date` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `department` | yes | `` | `chicago_contracts_raw.department` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `vendor_name` | yes | `` | `chicago_contracts_raw.vendor_name` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `vendor_id` | yes | `` | `chicago_contracts_raw.vendor_id` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `address_1` | no | `` | `` | no | `` | `database/chicago_loader.py:378` | explicitly excluded (Socrata $select projection omits this raw column) | Add this column to chicago_loader select_fields + parse/upsert mappings if required. |
| `address_2` | no | `` | `` | no | `` | `database/chicago_loader.py:378` | explicitly excluded (Socrata $select projection omits this raw column) | Add this column to chicago_loader select_fields + parse/upsert mappings if required. |
| `city` | yes | `` | `chicago_contracts_raw.city` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `state` | yes | `` | `chicago_contracts_raw.state` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `zip` | yes | `` | `chicago_contracts_raw.zip` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `award_amount` | yes | `` | `chicago_contracts_raw.award_amount` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `procurement_type` | yes | `` | `chicago_contracts_raw.procurement_type` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `contract_pdf` | yes | `` | `chicago_contracts_raw.contract_pdf` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |

### chicago_lobbying_activity_socrata_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `lobbying_activity_id` | yes | `` | `chicago_lobbying_activity_raw.lobbying_activity_id` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `period_start` | yes | `` | `chicago_lobbying_activity_raw.period_start` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `period_end` | yes | `` | `chicago_lobbying_activity_raw.period_end` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `action` | yes | `` | `chicago_lobbying_activity_raw.action` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `action_sought` | yes | `` | `chicago_lobbying_activity_raw.action_sought` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `department` | yes | `` | `chicago_lobbying_activity_raw.department` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `client_id` | yes | `` | `chicago_lobbying_activity_raw.client_id` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `client_name` | yes | `` | `chicago_lobbying_activity_raw.client_name` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `lobbyist_id` | yes | `` | `chicago_lobbying_activity_raw.lobbyist_id` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `lobbyist_first_name` | yes | `` | `chicago_lobbying_activity_raw.lobbyist_first_name` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `lobbyist_middle_initial` | no | `` | `` | no | `` | `database/chicago_loader.py:419` | explicitly excluded (Socrata $select projection omits this raw column) | Add this column to chicago_loader select_fields + parse/upsert mappings if required. |
| `lobbyist_last_name` | yes | `` | `chicago_lobbying_activity_raw.lobbyist_last_name` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `created_date` | no | `` | `` | no | `` | `database/chicago_loader.py:419` | explicitly excluded (Socrata $select projection omits this raw column) | Add this column to chicago_loader select_fields + parse/upsert mappings if required. |

### chicago_lobbyist_contributions_socrata_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `contribution_id` | yes | `` | `chicago_lobbyist_contributions_raw.contribution_id` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `period_start` | yes | `` | `chicago_lobbyist_contributions_raw.period_start` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `period_end` | yes | `` | `chicago_lobbyist_contributions_raw.period_end` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `contribution_date` | yes | `` | `chicago_lobbyist_contributions_raw.contribution_date` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `recipient` | yes | `` | `chicago_lobbyist_contributions_raw.recipient` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `amount` | yes | `` | `chicago_lobbyist_contributions_raw.amount` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `lobbyist_id` | yes | `` | `chicago_lobbyist_contributions_raw.lobbyist_id` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `lobbyist_first_name` | yes | `` | `chicago_lobbyist_contributions_raw.lobbyist_first_name` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `lobbyist_last_name` | yes | `` | `chicago_lobbyist_contributions_raw.lobbyist_last_name` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `created_date` | no | `` | `` | no | `` | `database/chicago_loader.py:407` | explicitly excluded (Socrata $select projection omits this raw column) | Add this column to chicago_loader select_fields + parse/upsert mappings if required. |

### chicago_payments_socrata_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `voucher_number` | yes | `` | `chicago_payments_raw.voucher_number` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `amount` | yes | `` | `chicago_payments_raw.amount` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `check_date` | yes | `check_date_raw` | `chicago_payments_raw.check_date_raw` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `department_name` | yes | `` | `chicago_payments_raw.department_name` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `contract_number` | yes | `` | `chicago_payments_raw.contract_number` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |
| `vendor_name` | yes | `` | `chicago_payments_raw.vendor_name` | no | `` | `` |  | Keep mapped; add downstream consumers or retire feed if staging-only is intentional. |

### fec_api_candidate_committees_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `affiliated_committee_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_ids` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `city` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `committee_id` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `committee_type` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `committee_type_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_city` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_name_1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_name_2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_name_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_name_middle` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_name_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_name_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_name_title` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_phone` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_state` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_street_1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_street_2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `custodian_zip` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `cycles` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `designation` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `designation_full` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `email` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `fax` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filing_frequency` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `first_f1_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `first_file_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `form_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_f1_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_file_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `leadership_pac` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `lobbyist_registrant_pac` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `name` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `organization_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `organization_type_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `party` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `party_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `party_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `party_type_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `sponsor_candidate_ids` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `state` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `state_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `street_1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `street_2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_city` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_name_1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_name_2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_name_middle` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_name_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_name_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_name_title` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_phone` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_state` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_street_1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_street_2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `treasurer_zip` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `website` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:938` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `zip` | yes | `` | `raw_extractions.payload_json; fec_candidate_committees.*` | yes | `database/federal_fec.py; webapp/routes/main.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |

### fec_api_candidate_totals_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `all_other_loans` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_contribution` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_election_year` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contribution_refunds` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributions` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `coverage_end_date` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `coverage_start_date` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `cycle` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `disbursements` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `election_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `exempt_legal_accounting_disbursement` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `federal_funds` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `fundraising_disbursements` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `individual_contributions` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `individual_itemized_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `individual_unitemized_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_beginning_image_number` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_cash_on_hand_end_period` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `last_debts_owed_by_committee` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_debts_owed_to_committee` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_net_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_net_operating_expenditures` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_report_type_full` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `last_report_year` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `loan_repayments` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `loan_repayments_candidate_loans` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `loan_repayments_other_loans` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `loans` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `loans_made_by_candidate` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `net_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `net_operating_expenditures` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `offsets_to_fundraising_expenditures` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `offsets_to_legal_accounting` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `offsets_to_operating_expenditures` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `operating_expenditures` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `other_disbursements` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `other_political_committee_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `other_receipts` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `political_party_committee_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `receipts` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `refunded_individual_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `refunded_other_political_committee_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `refunded_political_party_committee_contributions` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `total_offsets_to_operating_expenditures` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `transaction_coverage_date` | yes | `` | `raw_extractions.payload_json; fec_candidate_cycle_totals.*` | yes | `database/federal_fec.py; webapp/routes/main.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `transfers_from_other_authorized_committee` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `transfers_to_other_authorized_committee` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1052` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |

### fec_api_candidates_search_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `active_through` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_id` | yes | `` | `raw_extractions.payload_json; fec_candidate_match.*` | yes | `database/federal_fec.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_inactive` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_status` | yes | `` | `raw_extractions.payload_json; fec_candidate_match.*` | yes | `database/federal_fec.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `cycles` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `district` | yes | `` | `raw_extractions.payload_json; fec_candidate_match.*` | yes | `database/federal_fec.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `district_number` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `election_districts` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `election_years` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `federal_funds_flag` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `first_file_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `has_raised_funds` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `inactive_election_years` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `incumbent_challenge` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `incumbent_challenge_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_f2_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `last_file_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `load_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `name` | yes | `` | `raw_extractions.payload_json; fec_candidate_match.*` | yes | `database/federal_fec.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `office` | yes | `` | `raw_extractions.payload_json; fec_candidate_match.*` | yes | `database/federal_fec.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `office_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `party` | yes | `` | `raw_extractions.payload_json; fec_candidate_match.*` | yes | `database/federal_fec.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `party_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:802` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `principal_committees` | yes | `` | `raw_extractions.payload_json; fec_candidate_match.*` | yes | `database/federal_fec.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `state` | yes | `` | `raw_extractions.payload_json; fec_candidate_match.*` | yes | `database/federal_fec.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |

### fec_api_schedule_a_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `amendment_indicator` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `amendment_indicator_desc` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `back_reference_schedule_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `back_reference_transaction_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_first_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_last_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_middle_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_name` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_office` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_office_district` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_office_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_office_state` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_office_state_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `committee` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `committee_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `committee_name` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `conduit_committee_city` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_state` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_street1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_street2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_zip` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contribution_receipt_amount` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `contribution_receipt_date` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `contributor` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `contributor_aggregate_ytd` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_city` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `contributor_employer` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `contributor_first_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `contributor_last_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_middle_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_occupation` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `contributor_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_state` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `contributor_street_1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_street_2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `contributor_zip` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `donor_committee_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `election_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `election_type_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `entity_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `entity_type_desc` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `fec_election_type_desc` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `fec_election_year` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `file_number` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filing_form` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `image_number` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `increased_limit` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `is_individual` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `line_number` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `line_number_label` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `link_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `load_date` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `memo_code` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `memo_code_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `memo_text` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `memoed_subtotal` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `national_committee_nonfederal_account` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `original_sub_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `pdf_url` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `receipt_type` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `receipt_type_desc` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `receipt_type_full` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `recipient_committee_designation` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `recipient_committee_org_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `recipient_committee_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `report_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `report_year` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `schedule_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `schedule_type_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `sub_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `transaction_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `two_year_transaction_period` | yes | `` | `raw_extractions.payload_json; fec_schedule_a_contributions.*` | yes | `database/federal_fec.py; webapp/routes/main.py; database/analytics.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `unused_contbr_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1173` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |

### fec_api_schedule_b_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `amendment_indicator` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `amendment_indicator_desc` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `back_reference_schedule_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `back_reference_transaction_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `beneficiary_committee_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_first_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_last_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_middle_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_name` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_office` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_office_description` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_office_district` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_office_state` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_office_state_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `category_code` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `category_code_full` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `comm_dt` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `committee` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `committee_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_city` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_state` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_street1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_street2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_zip` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `disbursement_amount` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `disbursement_date` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `disbursement_description` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `disbursement_purpose_category` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `disbursement_type` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `disbursement_type_description` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `election_type` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `election_type_full` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `entity_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `entity_type_desc` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `fec_election_type_desc` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `fec_election_year` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `file_number` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filing_form` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `image_number` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `line_number` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `line_number_label` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `link_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `load_date` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `memo_code` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `memo_code_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `memo_text` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `memoed_subtotal` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `national_committee_nonfederal_account` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `original_sub_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_employer` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `payee_first_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_last_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_middle_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_occupation` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `payee_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `pdf_url` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `recipient_city` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `recipient_committee` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `recipient_committee_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `recipient_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `recipient_state` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `recipient_zip` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `ref_disp_excess_flg` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `report_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `report_year` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `schedule_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `schedule_type_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `semi_annual_bundled_refund` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `spender_committee_designation` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `spender_committee_org_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `spender_committee_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `sub_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `transaction_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `two_year_transaction_period` | yes | `` | `raw_extractions.payload_json; fec_schedule_b_disbursements.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `unused_recipient_committee_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1358` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |

### fec_api_schedule_e_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `action_code` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `action_code_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `amendment_indicator` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `amendment_number` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `back_reference_schedule_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `back_reference_transaction_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_first_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_last_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_middle_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_name` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_office` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_office_district` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_office_state` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `candidate_party` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `candidate_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `category_code` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `category_code_full` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `committee` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `committee_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `conduit_committee_city` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_state` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_street1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_street2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `conduit_committee_zip` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `disbursement_dt` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `dissemination_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `election_type` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `election_type_full` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `expenditure_amount` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `expenditure_date` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `expenditure_description` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `file_number` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filer_first_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filer_last_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filer_middle_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filer_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filer_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `filing_date` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `filing_form` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `form_line_number` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `image_number` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `independent_sign_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `independent_sign_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `is_notice` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `line_number` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `link_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `memo_code` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `memo_code_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `memo_text` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `memoed_subtotal` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `most_recent` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `notary_commission_expiration_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `notary_sign_date` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `notary_sign_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `office_total_ytd` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `original_sub_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_city` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `payee_first_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_last_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_middle_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_name` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_prefix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_state` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `payee_street_1` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_street_2` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_suffix` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `payee_zip` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `pdf_url` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `previous_file_number` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `report_type` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `report_year` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `schedule_type` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `schedule_type_full` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |
| `sub_id` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `support_oppose_indicator` | yes | `` | `raw_extractions.payload_json; fec_schedule_e_independent_expenditures.*` | yes | `database/federal_fec.py; webapp/routes/main.py; webapp/routes/federal_finance.py; scripts/newsroom_queries.py` | `` |  | Keep full raw payload retention and structured projection tests aligned. |
| `transaction_id` | yes | `` | `raw_extractions.payload_json` | no | `` | `database/federal_fec.py:1523` | explicitly excluded (not projected into structured FEC table) | Add explicit projection mapping if this key is needed downstream, or document intentional exclusion. |

### ilsos_lobbying_active_clients_csv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `CLIENT_ID` | yes | `client_id` | `lobbying_clients.client_id` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `CLIENT_NAME` | yes | `client_name` | `lobbying_clients.client_name` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENTITY_ID` | yes | `entity_id` | `lobbying_entities.entity_id` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENTITY_NAME` | yes | `entity_name` | `lobbying_entities.entity_name` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_REG_YEAR` | yes | `reg_year` | `lobbying_entities.reg_year` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |

### ilsos_lobbying_daily_csv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `CLIENT_ADDR1` | yes | `address_1` | `lobbying_clients.address_1` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `CLIENT_ADDR2` | yes | `address_2` | `lobbying_clients.address_2` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `CLIENT_CITY` | yes | `city` | `lobbying_clients.city` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `CLIENT_ID` | yes | `client_id` | `lobbying_clients.client_id` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `CLIENT_NAME` | yes | `client_name` | `lobbying_clients.client_name` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `CLIENT_STATUS` | yes | `status` | `lobbying_clients.status` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `CLIENT_ST_ABBR` | yes | `state` | `lobbying_clients.state` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `CLIENT_ZIP` | yes | `postal_code` | `lobbying_clients.postal_code` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_ADDR1` | yes | `address_1` | `lobbying_entities.address_1` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_ADDR2` | yes | `address_2` | `lobbying_entities.address_2` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_CITY` | yes | `city` | `lobbying_entities.city` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_ID` | yes | `entity_id` | `lobbying_entities.entity_id` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_NAME` | yes | `entity_name` | `lobbying_entities.entity_name` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_REG_YEAR` | yes | `reg_year` | `lobbying_entity_clients.reg_year` | yes | `webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_ST_ABBR` | yes | `state` | `lobbying_entities.state` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `ENT_ZIP` | yes | `postal_code` | `lobbying_entities.postal_code` | yes | `webapp/routes/lobbying.py; database/cross_matching.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_ADDR1` | yes | `address_1` | `lobbying_lobbyists.address_1` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_ADDR2` | yes | `address_2` | `lobbying_lobbyists.address_2` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_CITY` | yes | `city` | `lobbying_lobbyists.city` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_EMAIL` | yes | `email` | `lobbying_lobbyists.email` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_FNAME` | yes | `first_name` | `lobbying_lobbyists.first_name` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_ID` | yes | `lobbyist_id` | `lobbying_lobbyists.lobbyist_id` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_LNAME` | yes | `last_name` | `lobbying_lobbyists.last_name` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_MNAME` | yes | `middle_name` | `lobbying_lobbyists.middle_name` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_PHONE` | yes | `phone` | `lobbying_lobbyists.phone` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_STATUS` | yes | `status` | `lobbying_lobbyists.status` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_ST_ABBR` | yes | `state` | `lobbying_lobbyists.state` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |
| `LOBBYIST_ZIP` | yes | `postal_code` | `lobbying_lobbyists.postal_code` | yes | `webapp/routes/lobbying.py; docs/case_studies/ameren_lobbying_relationships.md` | `` |  | Keep mapped; add new-column alerting. |

### irs527_full_data_record_1_org_registration

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | yes | `form_id` | `irs527_organizations.form_id` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_2` | yes | `form_id_seq` | `irs527_organizations.form_id_seq` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_3` | no | `` | `` | no | `` | `database/irs527_loader.py:74` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_4` | no | `` | `` | no | `` | `database/irs527_loader.py:74` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_5` | no | `` | `` | no | `` | `database/irs527_loader.py:74` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_6` | yes | `ein` | `irs527_organizations.ein` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_7` | yes | `org_name` | `irs527_organizations.org_name` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_8` | yes | `address_1` | `irs527_organizations.address_1` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_9` | yes | `address_2` | `irs527_organizations.address_2` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_10` | yes | `city` | `irs527_organizations.city` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_11` | yes | `state` | `irs527_organizations.state` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_12` | yes | `zip` | `irs527_organizations.zip` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_13` | yes | `zip_ext` | `irs527_organizations.zip_ext` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_14` | yes | `email` | `irs527_organizations.email` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_15` | yes | `formation_date` | `irs527_organizations.formation_date` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_16` | yes | `custodian_name` | `irs527_organizations.custodian_name` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_17` | yes | `custodian_address_1` | `irs527_organizations.custodian_address_1` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_18` | yes | `custodian_address_2` | `irs527_organizations.custodian_address_2` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_19` | yes | `custodian_city` | `irs527_organizations.custodian_city` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_20` | yes | `custodian_state` | `irs527_organizations.custodian_state` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_21` | yes | `custodian_zip` | `irs527_organizations.custodian_zip` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_22` | yes | `custodian_zip_ext` | `irs527_organizations.custodian_zip_ext` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_23` | yes | `contact_name` | `irs527_organizations.contact_name` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_24` | yes | `contact_address_1` | `irs527_organizations.contact_address_1` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_25` | yes | `contact_address_2` | `irs527_organizations.contact_address_2` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_26` | yes | `contact_city` | `irs527_organizations.contact_city` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_27` | yes | `contact_state` | `irs527_organizations.contact_state` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_28` | yes | `contact_zip` | `irs527_organizations.contact_zip` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_29` | yes | `contact_zip_ext` | `irs527_organizations.contact_zip_ext` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_30` | yes | `business_address_1` | `irs527_organizations.business_address_1` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_31` | yes | `business_address_2` | `irs527_organizations.business_address_2` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_32` | yes | `business_city` | `irs527_organizations.business_city` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_33` | yes | `business_state` | `irs527_organizations.business_state` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_34` | yes | `business_zip` | `irs527_organizations.business_zip` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_35` | yes | `business_zip_ext` | `irs527_organizations.business_zip_ext` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_36` | yes | `purpose` | `irs527_organizations.purpose` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_37` | yes | `material_change_date` | `irs527_organizations.material_change_date` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_38` | yes | `insert_datetime` | `irs527_organizations.insert_datetime` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_39` | yes | `related_entity_bypass` | `irs527_organizations.related_entity_bypass` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_40` | yes | `eain_bypass` | `irs527_organizations.eain_bypass` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_41` | no | `` | `` | no | `` | `database/irs527_loader.py:74` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_42` | no | `` | `` | no | `` | `database/irs527_loader.py:74` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_43` | no | `` | `` | no | `` | `database/irs527_loader.py:74` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |

### irs527_full_data_record_2_periodic_report

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | yes | `ein_fallback` | `irs527_reports.ein_fallback` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_2` | yes | `form_id` | `irs527_reports.form_id` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_3` | yes | `period_start` | `irs527_reports.period_start` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_4` | yes | `period_end` | `irs527_reports.period_end` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_5` | no | `` | `` | no | `` | `database/irs527_loader.py:125` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_6` | no | `` | `` | no | `` | `database/irs527_loader.py:125` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_7` | no | `` | `` | no | `` | `database/irs527_loader.py:125` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_8` | no | `` | `` | no | `` | `database/irs527_loader.py:125` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_9` | yes | `org_name` | `irs527_reports.org_name` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_10` | yes | `org_ein` | `irs527_reports.org_ein` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_11` | yes | `org_address_1` | `irs527_reports.org_address_1` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_12` | yes | `org_address_2` | `irs527_reports.org_address_2` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_13` | yes | `org_city` | `irs527_reports.org_city` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_14` | yes | `org_state` | `irs527_reports.org_state` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_15` | yes | `org_zip` | `irs527_reports.org_zip` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_16` | yes | `org_zip_ext` | `irs527_reports.org_zip_ext` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_17` | yes | `email` | `irs527_reports.email` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_18` | yes | `formation_date` | `irs527_reports.formation_date` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_19` | yes | `custodian_name` | `irs527_reports.custodian_name` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_20` | yes | `custodian_address_1` | `irs527_reports.custodian_address_1` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_21` | yes | `custodian_address_2` | `irs527_reports.custodian_address_2` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_22` | yes | `custodian_city` | `irs527_reports.custodian_city` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_23` | yes | `custodian_state` | `irs527_reports.custodian_state` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_24` | yes | `custodian_zip` | `irs527_reports.custodian_zip` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_25` | yes | `custodian_zip_ext` | `irs527_reports.custodian_zip_ext` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_26` | yes | `contact_name` | `irs527_reports.contact_name` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_27` | yes | `contact_address_1` | `irs527_reports.contact_address_1` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_28` | yes | `contact_address_2` | `irs527_reports.contact_address_2` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_29` | yes | `contact_city` | `irs527_reports.contact_city` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_30` | yes | `contact_state` | `irs527_reports.contact_state` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_31` | yes | `contact_zip` | `irs527_reports.contact_zip` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_32` | yes | `contact_zip_ext` | `irs527_reports.contact_zip_ext` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_33` | yes | `business_address_1` | `irs527_reports.business_address_1` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_34` | yes | `business_address_2` | `irs527_reports.business_address_2` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_35` | yes | `business_city` | `irs527_reports.business_city` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_36` | yes | `business_state` | `irs527_reports.business_state` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_37` | yes | `business_zip` | `irs527_reports.business_zip` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_38` | yes | `business_zip_ext` | `irs527_reports.business_zip_ext` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_39` | yes | `qtr_indicator` | `irs527_reports.qtr_indicator` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_40` | yes | `monthly_amount_1` | `irs527_reports.monthly_amount_1` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_41` | yes | `monthly_amount_2` | `irs527_reports.monthly_amount_2` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_42` | yes | `monthly_amount_3` | `irs527_reports.monthly_amount_3` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_43` | yes | `total_contributions_fallback` | `irs527_reports.total_contributions_fallback` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_44` | yes | `total_expenditures_fallback` | `irs527_reports.total_expenditures_fallback` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_45` | yes | `total_contributions` | `irs527_reports.total_contributions` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_46` | no | `` | `` | no | `` | `database/irs527_loader.py:125` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_47` | yes | `total_expenditures` | `irs527_reports.total_expenditures` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_48` | yes | `insert_datetime` | `irs527_reports.insert_datetime` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_49` | no | `` | `` | no | `` | `database/irs527_loader.py:125` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |

### irs527_full_data_record_A_contribution

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | yes | `form_id` | `irs527_contributions.form_id` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_2` | no | `` | `` | no | `` | `database/irs527_loader.py:234` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_3` | yes | `org_name` | `irs527_contributions.org_name` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_4` | yes | `ein` | `irs527_contributions.ein` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_5` | yes | `contributor_name` | `irs527_contributions.contributor_name` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_6` | yes | `contributor_address` | `irs527_contributions.contributor_address` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_7` | yes | `contributor_address_2` | `irs527_contributions.contributor_address_2` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_8` | yes | `city` | `irs527_contributions.city` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_9` | yes | `state` | `irs527_contributions.state` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_10` | yes | `zip` | `irs527_contributions.zip` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_11` | yes | `zip_ext` | `irs527_contributions.zip_ext` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_12` | yes | `contributor_employer` | `irs527_contributions.contributor_employer` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_13` | yes | `amount` | `irs527_contributions.amount` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_14` | yes | `contributor_occupation` | `irs527_contributions.contributor_occupation` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_15` | yes | `date` | `irs527_contributions.date` | yes | `webapp/routes/irs527.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_16` | no | `` | `` | no | `` | `database/irs527_loader.py:234` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_17` | no | `` | `` | no | `` | `database/irs527_loader.py:234` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |

### irs527_full_data_record_B_expenditure

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | yes | `form_id` | `irs527_expenditures.form_id` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_2` | no | `` | `` | no | `` | `database/irs527_loader.py:259` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_3` | yes | `org_name` | `irs527_expenditures.org_name` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_4` | yes | `ein` | `irs527_expenditures.ein` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_5` | yes | `recipient_name` | `irs527_expenditures.recipient_name` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_6` | yes | `recipient_address` | `irs527_expenditures.recipient_address` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_7` | yes | `recipient_address_2` | `irs527_expenditures.recipient_address_2` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_8` | yes | `city` | `irs527_expenditures.city` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_9` | yes | `state` | `irs527_expenditures.state` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_10` | yes | `zip` | `irs527_expenditures.zip` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_11` | yes | `zip_ext` | `irs527_expenditures.zip_ext` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_12` | yes | `recipient_employer` | `irs527_expenditures.recipient_employer` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_13` | yes | `amount` | `irs527_expenditures.amount` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_14` | yes | `recipient_occupation` | `irs527_expenditures.recipient_occupation` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_15` | yes | `date` | `irs527_expenditures.date` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_16` | yes | `purpose` | `irs527_expenditures.purpose` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_17` | no | `` | `` | no | `` | `database/irs527_loader.py:259` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |

### irs527_full_data_record_D_director

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | yes | `form_id` | `irs527_directors.form_id` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_2` | no | `` | `` | no | `` | `database/irs527_loader.py:190` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_3` | yes | `org_name` | `irs527_directors.org_name` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_4` | yes | `ein` | `irs527_directors.ein` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_5` | yes | `person_name` | `irs527_directors.person_name` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_6` | yes | `title` | `irs527_directors.title` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_7` | yes | `address_1` | `irs527_directors.address_1` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_8` | yes | `address_2` | `irs527_directors.address_2` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_9` | yes | `city` | `irs527_directors.city` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_10` | yes | `state` | `irs527_directors.state` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_11` | yes | `zip` | `irs527_directors.zip` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_12` | yes | `zip_ext` | `irs527_directors.zip_ext` | yes | `webapp/routes/irs527.py; database/cross_matching.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_13` | no | `` | `` | no | `` | `database/irs527_loader.py:190` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |

### irs527_full_data_record_E_election_authority

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | yes | `form_id` | `irs527_election_authority.form_id` | yes | `database/irs527_loader.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_2` | yes | `election_authority_id` | `irs527_election_authority.election_authority_id` | yes | `database/irs527_loader.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_3` | yes | `state` | `irs527_election_authority.state` | yes | `database/irs527_loader.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_4` | no | `` | `` | no | `` | `database/irs527_loader.py:285` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_5` | no | `` | `` | no | `` | `database/irs527_loader.py:285` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |

### irs527_full_data_record_H_header

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | yes | `` | `` | no | `` | `database/irs527_loader.py:562` | always-null/meaningless for core model (header parsed for stats only) | Persist header metadata to an audit table if needed for provenance. |
| `pos_2` | yes | `` | `` | no | `` | `database/irs527_loader.py:562` | always-null/meaningless for core model (header parsed for stats only) | Persist header metadata to an audit table if needed for provenance. |
| `pos_3` | yes | `` | `` | no | `` | `database/irs527_loader.py:562` | always-null/meaningless for core model (header parsed for stats only) | Persist header metadata to an audit table if needed for provenance. |
| `pos_4` | no | `` | `` | no | `` | `database/irs527_loader.py:64` | never read | Add explicit mapping or mark intentional denylist with rationale. |

### irs527_full_data_record_R_related_org

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | yes | `form_id` | `irs527_related_orgs.form_id` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_2` | no | `` | `` | no | `` | `database/irs527_loader.py:212` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |
| `pos_3` | yes | `org_name` | `irs527_related_orgs.org_name` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_4` | yes | `ein` | `irs527_related_orgs.ein` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_5` | yes | `related_org_name` | `irs527_related_orgs.related_org_name` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_6` | yes | `relationship_type` | `irs527_related_orgs.relationship_type` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_7` | yes | `address_1` | `irs527_related_orgs.address_1` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_8` | yes | `address_2` | `irs527_related_orgs.address_2` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_9` | yes | `city` | `irs527_related_orgs.city` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_10` | yes | `state` | `irs527_related_orgs.state` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_11` | yes | `zip` | `irs527_related_orgs.zip` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_12` | yes | `zip_ext` | `irs527_related_orgs.zip_ext` | yes | `webapp/routes/irs527.py` | `` |  | Keep mapped; enforce positional schema checks in CI. |
| `pos_13` | no | `` | `` | no | `` | `database/irs527_loader.py:212` | explicitly excluded (parser never references this position) | Review IRS layout and map needed positions explicitly. |

### irs527_full_data_record_UNKNOWN

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `pos_1` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_2` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_3` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_4` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_5` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_6` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_7` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_8` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_9` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_10` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_11` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |
| `pos_12` | no | `` | `` | no | `` | `database/irs527_loader.py:651` | unknown/bug (unrecognized record type treated as malformed) | Persist unknown record types to quarantine table for review. |

### isbe_candidates_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Address1` | yes | `address_line_1` | `bulk_candidates_clean.address_line_1` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `Address2` | yes | `address_line_2` | `bulk_candidates_clean.address_line_2` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `City` | yes | `city` | `bulk_candidates_clean.city` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `District` | yes | `district` | `bulk_candidates_clean.district` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `DistrictType` | yes | `district_type` | `bulk_candidates_clean.district_type` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `FirstName` | yes | `first_name` | `bulk_candidates_clean.first_name` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `ID` | yes | `candidate_id` | `bulk_candidates_clean.candidate_id` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `LastName` | yes | `last_name` | `bulk_candidates_clean.last_name` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `Office` | yes | `office_sought` | `bulk_candidates_clean.office_sought` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `PartyAffiliation` | yes | `party_affiliation` | `bulk_candidates_clean.party_affiliation` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `RedactionRequested` | yes | `redaction_requested` | `bulk_candidates_clean.redaction_requested` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `ResidenceCounty` | yes | `residence_county` | `bulk_candidates_clean.residence_county` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `State` | yes | `state` | `bulk_candidates_clean.state` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |
| `Zip` | yes | `postal_code` | `bulk_candidates_clean.postal_code` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/cross_matching.py` | `` |  | Keep mapped; add schema regression tests. |

### isbe_canelections_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `CandidateID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ElectionType` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ElectionYear` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `FairCampaign` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `IncChallOpen` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `LimitsOff` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `LimitsOffReason` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `WonLost` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |

### isbe_cmte_candidate_links_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `CandidateID` | yes | `candidate_id` | `bulk_cmte_candidate_links_clean.candidate_id` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `CommitteeID` | yes | `committee_id_sbe` | `bulk_cmte_candidate_links_clean.committee_id_sbe` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `ID` | yes | `link_record_id` | `bulk_cmte_candidate_links_clean.link_record_id` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |

### isbe_cmte_officer_links_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `CommitteeID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `OfficerID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |

### isbe_committees_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Address1` | yes | `address_line_1` | `bulk_committees_clean.address_line_1` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Address2` | yes | `address_line_2` | `bulk_committees_clean.address_line_2` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Address3` | yes | `address_line_3` | `bulk_committees_clean.address_line_3` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `CanSuppOpp` | yes | `candidate_support_or_oppose` | `bulk_committees_clean.candidate_support_or_oppose` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `City` | yes | `city` | `bulk_committees_clean.city` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `CreationAmount` | yes | `creation_funds_available` | `bulk_committees_clean.creation_funds_available` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `CreationDate` | yes | `creation_date` | `bulk_committees_clean.creation_date` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `DispFunds95` | yes | `residual_funds_per_ilcs_9_5` | `bulk_committees_clean.residual_funds_per_ilcs_9_5` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `scripts/isbe_sunshine_etl.py:1243` | implicitly lost (collision with DispFundsDescrip -> disp_funds_95) | Preserve this column in sunshine-import (distinct target column) or commit an explicit denylist decision. |
| `DispFundsCharity` | yes | `residual_funds_to_charity` | `bulk_committees_clean.residual_funds_to_charity` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `DispFundsDescrip` | yes | `residual_funds_description` | `bulk_committees_clean.residual_funds_description` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `DispFundsPolComm` | yes | `residual_funds_to_political_committee` | `bulk_committees_clean.residual_funds_to_political_committee` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `DispFundsReturn` | yes | `residual_funds_return_to_contributors` | `bulk_committees_clean.residual_funds_return_to_contributors` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `ID` | yes | `committee_id_sbe` | `bulk_committees_clean.committee_id_sbe` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `LocalCommittee` | yes | `is_local_committee_obsolete` | `bulk_committees_clean.is_local_committee_obsolete` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `LocalID` | yes | `local_committee_id_obsolete` | `bulk_committees_clean.local_committee_id_obsolete` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `scripts/isbe_sunshine_etl.py:55` | explicitly excluded (sunshine FILE_COLUMNS omits LocalID) | Preserve this column in sunshine-import (distinct target column) or commit an explicit denylist decision. |
| `Name` | yes | `committee_name` | `bulk_committees_clean.committee_name` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `PartyAffiliation` | yes | `party_affiliation` | `bulk_committees_clean.party_affiliation` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `PolicySuppOpp` | yes | `policy_support_or_oppose` | `bulk_committees_clean.policy_support_or_oppose` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Purpose` | yes | `committee_purpose` | `bulk_committees_clean.committee_purpose` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `ReferName` | yes | `reference_name` | `bulk_committees_clean.reference_name` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `State` | yes | `state` | `bulk_committees_clean.state` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `StateCommittee` | yes | `is_state_committee_obsolete` | `bulk_committees_clean.is_state_committee_obsolete` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `StateID` | yes | `state_committee_id_obsolete` | `bulk_committees_clean.state_committee_id_obsolete` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `scripts/isbe_sunshine_etl.py:55` | explicitly excluded (sunshine FILE_COLUMNS omits StateID) | Preserve this column in sunshine-import (distinct target column) or commit an explicit denylist decision. |
| `Status` | yes | `committee_status_code` | `bulk_committees_clean.committee_status_code` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `StatusDate` | yes | `status_date` | `bulk_committees_clean.status_date` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `TypeOfCommittee` | yes | `committee_type` | `bulk_committees_clean.committee_type` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Zip` | yes | `postal_code` | `bulk_committees_clean.postal_code` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |

### isbe_d2totals_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Archived` | yes | `is_archived` | `bulk_d2_totals_clean.is_archived` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `BegFundsAvail` | yes | `beginning_funds_available` | `bulk_d2_totals_clean.beginning_funds_available` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `CommitteeID` | yes | `committee_id_sbe` | `bulk_d2_totals_clean.committee_id_sbe` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `DebtsI` | yes | `debts_obligations_itemized` | `bulk_d2_totals_clean.debts_obligations_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `DebtsNI` | yes | `debts_obligations_non_itemized` | `bulk_d2_totals_clean.debts_obligations_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `EndFundsAvail` | yes | `ending_funds_available` | `bulk_d2_totals_clean.ending_funds_available` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `ExpendI` | yes | `expenditures_itemized` | `bulk_d2_totals_clean.expenditures_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `ExpendNI` | yes | `expenditures_non_itemized` | `bulk_d2_totals_clean.expenditures_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `FiledDocID` | yes | `filed_doc_id` | `bulk_d2_totals_clean.filed_doc_id` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `ID` | yes | `d2_totals_record_id` | `bulk_d2_totals_clean.d2_totals_record_id` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `InKindI` | yes | `in_kind_contributions_itemized` | `bulk_d2_totals_clean.in_kind_contributions_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `InKindNI` | yes | `in_kind_contributions_non_itemized` | `bulk_d2_totals_clean.in_kind_contributions_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `IndependentExpI` | yes | `independent_expenditures_itemized` | `bulk_d2_totals_clean.independent_expenditures_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `IndependentExpNI` | yes | `independent_expenditures_non_itemized` | `bulk_d2_totals_clean.independent_expenditures_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `IndivContribI` | yes | `individual_contributions_itemized` | `bulk_d2_totals_clean.individual_contributions_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `IndivContribNI` | yes | `individual_contributions_non_itemized` | `bulk_d2_totals_clean.individual_contributions_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `LoanMadeI` | yes | `loans_made_itemized` | `bulk_d2_totals_clean.loans_made_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `scripts/isbe_sunshine_etl.py:1306` | implicitly lost (mapped to expenditures_itemized then overwritten by ExpendI) | Preserve this column in sunshine-import (distinct target column) or commit an explicit denylist decision. |
| `LoanMadeNI` | yes | `loans_made_non_itemized` | `bulk_d2_totals_clean.loans_made_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `scripts/isbe_sunshine_etl.py:1307` | implicitly lost (mapped to expenditures_non_itemized then overwritten by ExpendNI) | Preserve this column in sunshine-import (distinct target column) or commit an explicit denylist decision. |
| `LoanRcvI` | yes | `loans_received_itemized` | `bulk_d2_totals_clean.loans_received_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `LoanRcvNI` | yes | `loans_received_non_itemized` | `bulk_d2_totals_clean.loans_received_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `OtherRctI` | yes | `other_receipts_itemized` | `bulk_d2_totals_clean.other_receipts_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `OtherRctNI` | yes | `other_receipts_non_itemized` | `bulk_d2_totals_clean.other_receipts_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `TotalDebts` | yes | `total_debts_obligations` | `bulk_d2_totals_clean.total_debts_obligations` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `TotalExpend` | yes | `total_expenditures` | `bulk_d2_totals_clean.total_expenditures` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `TotalInKind` | yes | `total_in_kind_contributions` | `bulk_d2_totals_clean.total_in_kind_contributions` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `TotalInvest` | yes | `total_investments` | `bulk_d2_totals_clean.total_investments` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `TotalReceipts` | yes | `total_receipts` | `bulk_d2_totals_clean.total_receipts` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `XferInI` | yes | `transfers_in_itemized` | `bulk_d2_totals_clean.transfers_in_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `XferInNI` | yes | `transfers_in_non_itemized` | `bulk_d2_totals_clean.transfers_in_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `` |  | Keep mapped; add schema regression tests. |
| `XferOutI` | yes | `transfers_out_itemized` | `bulk_d2_totals_clean.transfers_out_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `scripts/isbe_sunshine_etl.py:1304` | implicitly lost (mapped to expenditures_itemized then overwritten by ExpendI) | Preserve this column in sunshine-import (distinct target column) or commit an explicit denylist decision. |
| `XferOutNI` | yes | `transfers_out_non_itemized` | `bulk_d2_totals_clean.transfers_out_non_itemized` | yes | `database/analytics.py; scripts/build_triple_pipeline.py` | `scripts/isbe_sunshine_etl.py:1305` | implicitly lost (mapped to expenditures_non_itemized then overwritten by ExpendNI) | Preserve this column in sunshine-import (distinct target column) or commit an explicit denylist decision. |

### isbe_expenditures_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Address1` | yes | `address_line_1` | `bulk_expenditures_clean.address_line_1` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Address2` | yes | `address_line_2` | `bulk_expenditures_clean.address_line_2` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `AggregateAmount` | yes | `aggregate_amount` | `bulk_expenditures_clean.aggregate_amount` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Amount` | yes | `amount` | `bulk_expenditures_clean.amount` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Archived` | yes | `is_archived` | `bulk_expenditures_clean.is_archived` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `CandidateName` | yes | `candidate_name` | `bulk_expenditures_clean.candidate_name` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `City` | yes | `city` | `bulk_expenditures_clean.city` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `CommitteeID` | yes | `committee_id_sbe` | `bulk_expenditures_clean.committee_id_sbe` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Country` | yes | `country` | `bulk_expenditures_clean.country` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `D2Part` | yes | `d2_part_code` | `bulk_expenditures_clean.d2_part_code` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `ETransID` | yes | `electronic_transaction_id` | `bulk_expenditures_clean.electronic_transaction_id` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `ExpendedDate` | yes | `expended_date` | `bulk_expenditures_clean.expended_date` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `FiledDocID` | yes | `filed_doc_id` | `bulk_expenditures_clean.filed_doc_id` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `FirstName` | yes | `payee_first_name` | `bulk_expenditures_clean.payee_first_name` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `ID` | yes | `expenditure_record_id` | `bulk_expenditures_clean.expenditure_record_id` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `LastOnlyName` | yes | `payee_last_or_business_name` | `bulk_expenditures_clean.payee_last_or_business_name` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Office` | yes | `office` | `bulk_expenditures_clean.office` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Opposing` | yes | `is_opposing` | `bulk_expenditures_clean.is_opposing` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Purpose` | yes | `purpose` | `bulk_expenditures_clean.purpose` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `RedactionRequested` | yes | `redaction_requested` | `bulk_expenditures_clean.redaction_requested` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `State` | yes | `state` | `bulk_expenditures_clean.state` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Supporting` | yes | `is_supporting` | `bulk_expenditures_clean.is_supporting` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |
| `Zip` | yes | `postal_code` | `bulk_expenditures_clean.postal_code` | yes | `webapp/routes/committees.py; database/analytics.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; add schema regression tests. |

### isbe_filed_docs_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Amend` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Archived` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `B9SignerFirstName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `B9SignerLastOnlyName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Clarification` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Comment` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `CommitteeID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `DocName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ElectionType` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ElectionYear` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `FiledDocType` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Pages` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Provider` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `RcvdAt` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `RcvdDateTime` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `RedactionRequested` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `RptPdBegDate` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `RptPdEndDate` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SbmttrAddress1` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SbmttrAddress2` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SbmttrCity` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SbmttrFirstName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SbmttrLastOnlyName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SbmttrState` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SbmttrZip` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SignerFirstName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `SignerLastOnlyName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Source` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |

### isbe_investments_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Address1` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Address2` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Archived` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `City` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `CommitteeID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Country` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `CurrentValue` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Description` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `FiledDocID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `FirstName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `LastOnlyName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `LiquidDate` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `LiquidValue` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `PurchaseDate` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `PurchasePrice` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `PurchaseShares` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `State` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Zip` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |

### isbe_officers_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Address1` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Address2` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `City` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `FirstName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `LastName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Phone` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `RedactionRequested` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `State` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Title` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Zip` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |

### isbe_prev_officers_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Address1` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Address2` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `City` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `CommitteeID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `FirstName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ID` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `LastName` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `RedactionRequested` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `ResignDate` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `State` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Title` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |
| `Zip` | no | `` | `` | no | `` | `database/bulk_download_loader.py:1362` | explicitly excluded (import-bulk-download never selects this file type) | Either migrate consumers to sunshine-import outputs or extend import-bulk-download to include this file family. |

### isbe_receipts_tsv

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `Address1` | yes | `address_line_1` | `bulk_receipts_clean.address_line_1` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Address2` | yes | `address_line_2` | `bulk_receipts_clean.address_line_2` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `AggregateAmount` | yes | `aggregate_amount` | `bulk_receipts_clean.aggregate_amount` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Amount` | yes | `amount` | `bulk_receipts_clean.amount` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Archived` | yes | `is_archived` | `bulk_receipts_clean.is_archived` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `City` | yes | `city` | `bulk_receipts_clean.city` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `CommitteeID` | yes | `committee_id_sbe` | `bulk_receipts_clean.committee_id_sbe` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Country` | yes | `country` | `bulk_receipts_clean.country` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `D2Part` | yes | `d2_part_code` | `bulk_receipts_clean.d2_part_code` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Description` | yes | `description` | `bulk_receipts_clean.description` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `ETransID` | yes | `electronic_transaction_id` | `bulk_receipts_clean.electronic_transaction_id` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Employer` | yes | `employer` | `bulk_receipts_clean.employer` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `FiledDocID` | yes | `filed_doc_id` | `bulk_receipts_clean.filed_doc_id` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `FirstName` | yes | `first_name` | `bulk_receipts_clean.first_name` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `ID` | yes | `receipt_record_id` | `bulk_receipts_clean.receipt_record_id` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `LastOnlyName` | yes | `last_or_business_name` | `bulk_receipts_clean.last_or_business_name` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `LoanAmount` | yes | `loan_amount` | `bulk_receipts_clean.loan_amount` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Occupation` | yes | `occupation` | `bulk_receipts_clean.occupation` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `RcvDate` | yes | `received_date` | `bulk_receipts_clean.received_date` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `RedactionRequested` | yes | `redaction_requested` | `bulk_receipts_clean.redaction_requested` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `State` | yes | `state` | `bulk_receipts_clean.state` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `VendorAddress1` | yes | `vendor_address_line_1` | `bulk_receipts_clean.vendor_address_line_1` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `VendorAddress2` | yes | `vendor_address_line_2` | `bulk_receipts_clean.vendor_address_line_2` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `VendorCity` | yes | `vendor_city` | `bulk_receipts_clean.vendor_city` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `VendorFirstName` | yes | `vendor_first_name` | `bulk_receipts_clean.vendor_first_name` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `VendorLastOnlyName` | yes | `vendor_last_or_business_name` | `bulk_receipts_clean.vendor_last_or_business_name` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `VendorState` | yes | `vendor_state` | `bulk_receipts_clean.vendor_state` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `VendorZip` | yes | `vendor_postal_code` | `bulk_receipts_clean.vendor_postal_code` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |
| `Zip` | yes | `postal_code` | `bulk_receipts_clean.postal_code` | yes | `webapp/routes/committees.py; webapp/routes/lobbying.py; database/analytics.py` | `` |  | Keep mapped; add schema regression tests. |

### openbook_contract_detail_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `contract_number` | yes | `payload_json` | `raw_extractions.payload_json` | yes | `database/federal_fec.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `detail_url` | yes | `payload_json` | `raw_extractions.payload_json` | yes | `database/federal_fec.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `fiscal_year` | yes | `payload_json` | `raw_extractions.payload_json` | yes | `database/federal_fec.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `vendor_key` | yes | `payload_json` | `raw_extractions.payload_json` | yes | `database/federal_fec.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `warrant_count` | yes | `payload_json` | `raw_extractions.payload_json` | yes | `database/federal_fec.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `warrants` | yes | `payload_json` | `raw_extractions.payload_json` | yes | `database/federal_fec.py; scraper/openbook_scraper.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |

### openbook_contracts_search_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `agency_code` | yes | `` | `openbook_contracts_raw.agency_code` | yes | `webapp/routes/openbook.py; webapp/routes/main.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `agency_name` | yes | `` | `openbook_contracts_raw.agency_name` | yes | `webapp/routes/openbook.py; webapp/routes/main.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `award_amount` | yes | `` | `openbook_contracts_raw.award_amount` | yes | `webapp/routes/openbook.py; webapp/routes/main.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `contract_number` | yes | `` | `openbook_contracts_raw.contract_number` | yes | `webapp/routes/openbook.py; webapp/routes/main.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `detail_url` | yes | `` | `openbook_contracts_raw.detail_url` | yes | `webapp/routes/openbook.py; webapp/routes/main.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `fiscal_year` | yes | `` | `openbook_contracts_raw.fiscal_year` | yes | `webapp/routes/openbook.py; webapp/routes/main.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `row_hash` | yes | `` | `openbook_contracts_raw.row_hash` | yes | `webapp/routes/openbook.py; webapp/routes/main.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `vendor_label` | yes | `` | `openbook_contracts_raw.vendor_label` | yes | `webapp/routes/openbook.py; webapp/routes/main.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |

### openbook_contributions_tab_json

| raw_column | is_loaded_from_raw? | is_renamed_to? | is_stored? (table.column) | is_used? | where_used | where_dropped | drop_reason | recommendation |
|---|---|---|---|---|---|---|---|---|
| `amount` | yes | `` | `openbook_contributions_raw.amount` | yes | `webapp/routes/openbook.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `contribution_date` | yes | `` | `openbook_contributions_raw.contribution_date` | yes | `webapp/routes/openbook.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `contributor_first_name` | yes | `` | `openbook_contributions_raw.contributor_first_name` | yes | `webapp/routes/openbook.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `contributor_name` | yes | `` | `openbook_contributions_raw.contributor_name` | yes | `webapp/routes/openbook.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `employer` | yes | `` | `openbook_contributions_raw.employer` | yes | `webapp/routes/openbook.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `recipient_name` | yes | `` | `openbook_contributions_raw.recipient_name` | yes | `webapp/routes/openbook.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |
| `row_hash` | yes | `` | `openbook_contributions_raw.row_hash` | yes | `webapp/routes/openbook.py` | `` |  | Keep mapped; retain raw snapshot payloads for auditability. |

## 3) Drop reasons taxonomy

- explicitly excluded: 387
  - `isbe_committees_tsv.LocalID` -> `scripts/isbe_sunshine_etl.py:55` (explicitly excluded (sunshine FILE_COLUMNS omits LocalID))
  - `isbe_committees_tsv.StateID` -> `scripts/isbe_sunshine_etl.py:55` (explicitly excluded (sunshine FILE_COLUMNS omits StateID))
  - `isbe_filed_docs_tsv.Amend` -> `database/bulk_download_loader.py:1362` (explicitly excluded (import-bulk-download never selects this file type))
  - `isbe_filed_docs_tsv.Archived` -> `database/bulk_download_loader.py:1362` (explicitly excluded (import-bulk-download never selects this file type))
  - `isbe_filed_docs_tsv.B9SignerFirstName` -> `database/bulk_download_loader.py:1362` (explicitly excluded (import-bulk-download never selects this file type))
  - `isbe_filed_docs_tsv.B9SignerLastOnlyName` -> `database/bulk_download_loader.py:1362` (explicitly excluded (import-bulk-download never selects this file type))
  - `isbe_filed_docs_tsv.Clarification` -> `database/bulk_download_loader.py:1362` (explicitly excluded (import-bulk-download never selects this file type))
  - `isbe_filed_docs_tsv.Comment` -> `database/bulk_download_loader.py:1362` (explicitly excluded (import-bulk-download never selects this file type))
  - `isbe_filed_docs_tsv.CommitteeID` -> `database/bulk_download_loader.py:1362` (explicitly excluded (import-bulk-download never selects this file type))
  - `isbe_filed_docs_tsv.DocName` -> `database/bulk_download_loader.py:1362` (explicitly excluded (import-bulk-download never selects this file type))
- implicitly lost: 5
  - `isbe_committees_tsv.DispFunds95` -> `scripts/isbe_sunshine_etl.py:1243` (implicitly lost (collision with DispFundsDescrip -> disp_funds_95))
  - `isbe_d2totals_tsv.LoanMadeI` -> `scripts/isbe_sunshine_etl.py:1306` (implicitly lost (mapped to expenditures_itemized then overwritten by ExpendI))
  - `isbe_d2totals_tsv.LoanMadeNI` -> `scripts/isbe_sunshine_etl.py:1307` (implicitly lost (mapped to expenditures_non_itemized then overwritten by ExpendNI))
  - `isbe_d2totals_tsv.XferOutI` -> `scripts/isbe_sunshine_etl.py:1304` (implicitly lost (mapped to expenditures_itemized then overwritten by ExpendI))
  - `isbe_d2totals_tsv.XferOutNI` -> `scripts/isbe_sunshine_etl.py:1305` (implicitly lost (mapped to expenditures_non_itemized then overwritten by ExpendNI))
- always-null: 3
  - `irs527_full_data_record_H_header.pos_1` -> `database/irs527_loader.py:562` (always-null/meaningless for core model (header parsed for stats only))
  - `irs527_full_data_record_H_header.pos_2` -> `database/irs527_loader.py:562` (always-null/meaningless for core model (header parsed for stats only))
  - `irs527_full_data_record_H_header.pos_3` -> `database/irs527_loader.py:562` (always-null/meaningless for core model (header parsed for stats only))
- unknown/bug: 13
  - `irs527_full_data_record_H_header.pos_4` -> `database/irs527_loader.py:64` (never read)
  - `irs527_full_data_record_UNKNOWN.pos_1` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))
  - `irs527_full_data_record_UNKNOWN.pos_2` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))
  - `irs527_full_data_record_UNKNOWN.pos_3` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))
  - `irs527_full_data_record_UNKNOWN.pos_4` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))
  - `irs527_full_data_record_UNKNOWN.pos_5` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))
  - `irs527_full_data_record_UNKNOWN.pos_6` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))
  - `irs527_full_data_record_UNKNOWN.pos_7` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))
  - `irs527_full_data_record_UNKNOWN.pos_8` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))
  - `irs527_full_data_record_UNKNOWN.pos_9` -> `database/irs527_loader.py:651` (unknown/bug (unrecognized record type treated as malformed))

## 4) Not-skipping hardening recommendations

1. Add `schemas/raw_schema_allowlist.yml` per feed and enforce: `raw_columns <= staging_columns OR explicit_denylist`.
2. Run `scripts/raw_schema_census.py` in CI and fail on newly-seen columns unless allowlist is updated with rationale.
3. Capture per-run raw schema artifacts (`output/raw_schema_census.json`) and store a dated copy for regression diffing.
4. In sunshine ETL, stop key-collision mapping (`DispFunds95`/`DispFundsDescrip`, D2 transfer/loan fields) and store separate target columns.
5. Extend `import-bulk-download` to ingest the six currently ignored ISBE files or deprecate that path in favor of sunshine-import.
6. Add row-level reject telemetry dashboards for `bulk_expenditures_rejects` and alert if reject rates spike.
7. Persist unknown IRS record types into a quarantine table instead of counting only as malformed.

## Blocked / partial feeds

- `openbook_employees_tab_json` blocked: No matching raw_extractions rows for source_type pattern. (raw_globs=sqlite:data/campaign_finance.db raw_extractions source_type LIKE openbook_employees_tab)
- ISBE site scrapers and Comptroller HTML scraper remain partially blocked for full raw proof because complete per-run raw HTML snapshots are not retained.

Needed to unblock remaining gaps: retain full raw HTML/API snapshots per run (before parsing) and keep feed-specific allowlists with rationale.
