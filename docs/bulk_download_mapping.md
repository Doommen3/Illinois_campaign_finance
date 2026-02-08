# Bulk Download Columns and Joins

## Files inspected
- `/Users/devin/Illinois_campaign_finance/Bulk_download/committees_639060038163077225.txt`
- `/Users/devin/Illinois_campaign_finance/Bulk_download/d2totals_639060039113297425.txt`
- `/Users/devin/Illinois_campaign_finance/Bulk_download/candidates_639060037834639303.txt`
- `/Users/devin/Illinois_campaign_finance/Bulk_download/cmtecandidatelinks_639060047756941723.txt`
- `/Users/devin/Illinois_campaign_finance/Bulk_download/receipts_639060048226403871.txt`
- Dictionary reference: `/Users/devin/Illinois_campaign_finance/Bulk_download/campaigndisclosuredatadictionary_639060037025890016.txt`

## Join strategy
- Committee financial join:
  - `committees.ID` = `d2totals.CommitteeID`
  - Purpose: attach committee identity/profile fields to each D-2 totals filing row.
- Candidate committee link join:
  - `cmtecandidatelinks.CommitteeID` = `committees.ID`
  - `cmtecandidatelinks.CandidateID` = `candidates.ID`
  - Purpose: connect committees to candidate profiles.
- Candidate + committee + D2 join:
  - `cmtecandidatelinks.CommitteeID` = `d2totals.CommitteeID`
  - Purpose: analyze committee filings by linked candidate.
- Committee receipts join:
  - `receipts.CommitteeID` = `committees.ID`
  - Purpose: attach committee metadata to each receipt/itemized row.
- D2-to-receipts reconciliation:
  - `receipts.CommitteeID` + `receipts.FiledDocID` = `d2totals.CommitteeID` + `d2totals.FiledDocID`
  - Purpose: compare D2 filing totals against itemized receipt-level sums.
- Candidate + committee + receipts aggregate:
  - `cmtecandidatelinks.CommitteeID` = `receipts.CommitteeID`
  - Purpose: connect candidate-linked committees to receipt-level contribution/expenditure totals.
- Validation metric included:
  - `cmtecandidatelinks.ID` vs `candidates.ID` overlap is tracked as a diagnostic only.
  - It is not used as the primary relational join.

## Normalized tables created
- `bulk_committees_clean`
- `bulk_d2_totals_clean`
- `bulk_candidates_clean`
- `bulk_cmte_candidate_links_clean`
- `bulk_receipts_clean`
- `bulk_committee_d2_totals` (joined output table)
- `bulk_committee_candidate_links` (committee + candidate link output)
- `bulk_candidate_committee_d2_totals` (candidate + committee + filing rows)
- `bulk_candidate_committee_finance_agg` (candidate + committee rollups)
- `bulk_committee_receipts` (committee + receipt rows)
- `bulk_d2_receipts_recon` (D2 totals compared to receipt sums by filing)
- `bulk_candidate_committee_receipts_agg` (candidate + committee receipt rollups)

## Committees column rename map
| Raw column | Renamed column |
|---|---|
| `ID` | `committee_id_sbe` |
| `TypeOfCommittee` | `committee_type` |
| `StateCommittee` | `is_state_committee_obsolete` |
| `StateID` | `state_committee_id_obsolete` |
| `LocalCommittee` | `is_local_committee_obsolete` |
| `LocalID` | `local_committee_id_obsolete` |
| `ReferName` | `reference_name` |
| `Name` | `committee_name` |
| `Address1` | `address_line_1` |
| `Address2` | `address_line_2` |
| `Address3` | `address_line_3` |
| `City` | `city` |
| `State` | `state` |
| `Zip` | `postal_code` |
| `Status` | `committee_status_code` |
| `StatusDate` | `status_date` |
| `CreationDate` | `creation_date` |
| `CreationAmount` | `creation_funds_available` |
| `DispFundsReturn` | `residual_funds_return_to_contributors` |
| `DispFundsPolComm` | `residual_funds_to_political_committee` |
| `DispFundsCharity` | `residual_funds_to_charity` |
| `DispFunds95` | `residual_funds_per_ilcs_9_5` |
| `DispFundsDescrip` | `residual_funds_description` |
| `CanSuppOpp` | `candidate_support_or_oppose` |
| `PolicySuppOpp` | `policy_support_or_oppose` |
| `PartyAffiliation` | `party_affiliation` |
| `Purpose` | `committee_purpose` |

## D2Totals column rename map
| Raw column | Renamed column |
|---|---|
| `ID` | `d2_totals_record_id` |
| `CommitteeID` | `committee_id_sbe` |
| `FiledDocID` | `filed_doc_id` |
| `BegFundsAvail` | `beginning_funds_available` |
| `IndivContribI` | `individual_contributions_itemized` |
| `IndivContribNI` | `individual_contributions_non_itemized` |
| `XferInI` | `transfers_in_itemized` |
| `XferInNI` | `transfers_in_non_itemized` |
| `LoanRcvI` | `loans_received_itemized` |
| `LoanRcvNI` | `loans_received_non_itemized` |
| `OtherRctI` | `other_receipts_itemized` |
| `OtherRctNI` | `other_receipts_non_itemized` |
| `TotalReceipts` | `total_receipts` |
| `InKindI` | `in_kind_contributions_itemized` |
| `InKindNI` | `in_kind_contributions_non_itemized` |
| `TotalInKind` | `total_in_kind_contributions` |
| `XferOutI` | `transfers_out_itemized` |
| `XferOutNI` | `transfers_out_non_itemized` |
| `LoanMadeI` | `loans_made_itemized` |
| `LoanMadeNI` | `loans_made_non_itemized` |
| `ExpendI` | `expenditures_itemized` |
| `ExpendNI` | `expenditures_non_itemized` |
| `IndependentExpI` | `independent_expenditures_itemized` |
| `IndependentExpNI` | `independent_expenditures_non_itemized` |
| `TotalExpend` | `total_expenditures` |
| `DebtsI` | `debts_obligations_itemized` |
| `DebtsNI` | `debts_obligations_non_itemized` |
| `TotalDebts` | `total_debts_obligations` |
| `TotalInvest` | `total_investments` |
| `EndFundsAvail` | `ending_funds_available` |
| `Archived` | `is_archived` |

## Candidates column rename map
| Raw column | Renamed column |
|---|---|
| `ID` | `candidate_id` |
| `LastName` | `last_name` |
| `FirstName` | `first_name` |
| `Address1` | `address_line_1` |
| `Address2` | `address_line_2` |
| `City` | `city` |
| `State` | `state` |
| `Zip` | `postal_code` |
| `Office` | `office_sought` |
| `DistrictType` | `district_type` |
| `District` | `district` |
| `ResidenceCounty` | `residence_county` |
| `PartyAffiliation` | `party_affiliation` |
| `RedactionRequested` | `redaction_requested` |

## CmteCandidateLinks column rename map
| Raw column | Renamed column |
|---|---|
| `ID` | `link_record_id` |
| `CommitteeID` | `committee_id_sbe` |
| `CandidateID` | `candidate_id` |

## Receipts column rename map
| Raw column | Renamed column |
|---|---|
| `ID` | `receipt_record_id` |
| `CommitteeID` | `committee_id_sbe` |
| `FiledDocID` | `filed_doc_id` |
| `ETransID` | `electronic_transaction_id` |
| `LastOnlyName` | `last_or_business_name` |
| `FirstName` | `first_name` |
| `RcvDate` | `received_date` |
| `Amount` | `amount` |
| `AggregateAmount` | `aggregate_amount` |
| `LoanAmount` | `loan_amount` |
| `Occupation` | `occupation` |
| `Employer` | `employer` |
| `Address1` | `address_line_1` |
| `Address2` | `address_line_2` |
| `City` | `city` |
| `State` | `state` |
| `Zip` | `postal_code` |
| `D2Part` | `d2_part_code` |
| `Description` | `description` |
| `VendorLastOnlyName` | `vendor_last_or_business_name` |
| `VendorFirstName` | `vendor_first_name` |
| `VendorAddress1` | `vendor_address_line_1` |
| `VendorAddress2` | `vendor_address_line_2` |
| `VendorCity` | `vendor_city` |
| `VendorState` | `vendor_state` |
| `VendorZip` | `vendor_postal_code` |
| `Archived` | `is_archived` |
| `Country` | `country` |
| `RedactionRequested` | `redaction_requested` |

## Query example
```sql
SELECT committee_name, party_affiliation, filed_doc_id, total_receipts, total_expenditures, ending_funds_available
FROM bulk_committee_d2_totals
WHERE committee_id_sbe = 10125
ORDER BY filed_doc_id DESC;
```

## Candidate aggregation query example
```sql
SELECT candidate_full_name, office_sought, committee_name, filing_count, sum_total_receipts, sum_total_expenditures
FROM bulk_candidate_committee_finance_agg
WHERE candidate_id = 12345
ORDER BY sum_total_receipts DESC;
```

## Receipts query examples
```sql
SELECT committee_name, filed_doc_id, d2_part_code, SUM(amount) AS sum_amount, COUNT(*) AS row_count
FROM bulk_committee_receipts
WHERE committee_id_sbe = 10125
GROUP BY committee_name, filed_doc_id, d2_part_code
ORDER BY filed_doc_id DESC, d2_part_code;
```

```sql
SELECT committee_name, filed_doc_id, d2_total_receipts, receipts_amount_sum, receipts_minus_d2_total
FROM bulk_d2_receipts_recon
WHERE ABS(receipts_minus_d2_total) > 0.01
ORDER BY ABS(receipts_minus_d2_total) DESC
LIMIT 100;
```
