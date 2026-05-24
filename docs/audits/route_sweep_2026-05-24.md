# Production Route Sweep — 2026-05-24 03:00 UTC

- **Host:** `https://www.followthemoneyil.com`
- **Periods tested:** 2026cycle, all
- **Routes hit:** 202
- **Routes skipped (missing seeds):** 14

## Summary by category

| Category | Count |
|---|---|
| 4xx | 12 |
| network-error | 3 |
| 200-empty | 20 |
| 200-ok | 123 |
| auth-gated | 44 |
| skipped | 14 |

## 4xx (12)

| Status | ms | Bytes | Period | URL | Markers |
|---:|---:|---:|---|---|---|
| 404 | 1895 | 7746 | 2026cycle | `/experimental/viz-lab/data/triple-pipeline/entities?period=2026cycle` |  |
| 404 | 783 | 7632 | all | `/experimental/viz-lab/data/triple-pipeline/entities?period=all` |  |
| 404 | 782 | 7746 | 2026cycle | `/experimental/viz-lab/data/triple-pipeline/graph?period=2026cycle` |  |
| 404 | 767 | 7746 | 2026cycle | `/experimental/viz-lab/data/triple-pipeline/ego?period=2026cycle` |  |
| 404 | 766 | 7632 | all | `/experimental/viz-lab/data/triple-pipeline/ego?period=all` |  |
| 404 | 738 | 7746 | 2026cycle | `/experimental/viz-lab/data/geometry/il/cd119?period=2026cycle` |  |
| 404 | 666 | 7632 | all | `/experimental/viz-lab/data/triple-pipeline/graph?period=all` |  |
| 404 | 535 | 7632 | all | `/experimental/viz-lab/data/geometry/il/cd119?period=all` |  |
| 404 | 440 | 39 | 2026cycle | `/federal-finance/committees/C00305920/receipts?period=2026cycle` |  |
| 404 | 371 | 39 | all | `/federal-finance/committees/C00305920/receipts?period=all` |  |
| 404 | 95 | 30 | all | `/experimental/viz-lab/data/triple-pipeline?period=all` |  |
| 404 | 94 | 30 | 2026cycle | `/experimental/viz-lab/data/triple-pipeline?period=2026cycle` |  |

## network-error (3)

| Status | ms | Bytes | Period | URL | Markers |
|---:|---:|---:|---|---|---|
| - | 30060 | 0 | 2026cycle | `/lobbying/flows/data?period=2026cycle` | net-error: The read operation timed out |
| - | 30049 | 0 | all | `/analytics/state-races/governor-statewide-illinois-f72f709631?period=all` | net-error: The read operation timed out |
| - | 30041 | 0 | all | `/lobbying/flows/data?period=all` | net-error: The read operation timed out |

## 200-empty (20)

| Status | ms | Bytes | Period | URL | Markers |
|---:|---:|---:|---|---|---|
| 200 | 6118 | 3 | all | `/527/suggest?period=all` | ∅ empty-json-payload |
| 200 | 3336 | 31820 | all | `/experimental/viz-lab/triple-pipeline?period=all` | ∅ <tbody></tbody>,<tbody>
</tbody> |
| 200 | 3116 | 11132 | 2026cycle | `/analytics/geo-drilldown?period=2026cycle` | ∅ no matching |
| 200 | 2128 | 3 | 2026cycle | `/527/suggest?period=2026cycle` | ∅ empty-json-payload |
| 200 | 2007 | 31820 | all | `/experimental/viz-lab/triple-pipeline?period=all` | ∅ <tbody></tbody>,<tbody>
</tbody> |
| 200 | 1916 | 11635 | 2026cycle | `/federal-finance/geo-drilldown?period=2026cycle` | ∅ no matching |
| 200 | 1883 | 11455 | all | `/federal-finance/geo-drilldown?period=all` | ∅ no matching |
| 200 | 1620 | 10704 | all | `/analytics/geo-drilldown?period=all` | ∅ no matching |
| 200 | 1520 | 3 | 2026cycle | `/lobbying/flows/suggest?period=2026cycle` | ∅ empty-json-payload |
| 200 | 1356 | 32115 | 2026cycle | `/experimental/viz-lab/triple-pipeline?period=2026cycle` | ∅ <tbody></tbody>,<tbody>
</tbody> |
| 200 | 788 | 32115 | 2026cycle | `/experimental/viz-lab/triple-pipeline?period=2026cycle` | ∅ <tbody></tbody>,<tbody>
</tbody> |
| 200 | 769 | 32115 | 2026cycle | `/experimental/viz-lab/?period=2026cycle` | ∅ <tbody></tbody>,<tbody>
</tbody> |
| 200 | 747 | 32115 | 2026cycle | `/experimental/viz-lab?period=2026cycle` | ∅ <tbody></tbody>,<tbody>
</tbody> |
| 200 | 738 | 31820 | all | `/experimental/viz-lab/?period=all` | ∅ <tbody></tbody>,<tbody>
</tbody> |
| 200 | 578 | 31820 | all | `/experimental/viz-lab?period=all` | ∅ <tbody></tbody>,<tbody>
</tbody> |
| 200 | 114 | 3 | all | `/lobbying/flows/suggest?period=all` | ∅ empty-json-payload |
| 200 | 102 | 3 | 2026cycle | `/committees/suggest?period=2026cycle` | ∅ empty-json-payload |
| 200 | 101 | 3 | 2026cycle | `/lobbying/suggest?period=2026cycle` | ∅ empty-json-payload |
| 200 | 101 | 3 | all | `/lobbying/suggest?period=all` | ∅ empty-json-payload |
| 200 | 96 | 3 | all | `/committees/suggest?period=all` | ∅ empty-json-payload |

## 200-ok (123)

| Status | ms | Bytes | Period | URL | Markers |
|---:|---:|---:|---|---|---|
| 200 | 28769 | 98713 | all | `/candidate-finance/?period=all` |  |
| 200 | 21296 | 200000 | all | `/live-feed?period=all` |  |
| 200 | 19274 | 46239 | all | `/analytics/geography?period=all` |  |
| 200 | 15631 | 28971 | 2026cycle | `/auth/logout?period=2026cycle` |  |
| 200 | 15566 | 200000 | all | `/federal-finance/matching?period=all` |  |
| 200 | 15512 | 40948 | 2026cycle | `/d2-expenditures-reconciliation/?period=2026cycle` |  |
| 200 | 14852 | 40821 | all | `/d2-expenditures-reconciliation/?period=all` |  |
| 200 | 14820 | 21003 | all | `/analytics/donors?period=all` |  |
| 200 | 13694 | 35238 | all | `/527/dark-money?period=all` |  |
| 200 | 13482 | 28971 | all | `/auth/logout?period=all` |  |
| 200 | 12970 | 200000 | 2026cycle | `/federal-finance/matching?period=2026cycle` |  |
| 200 | 10869 | 28432 | all | `/527/?period=all` |  |
| 200 | 9825 | 99051 | 2026cycle | `/candidate-finance/?period=2026cycle` |  |
| 200 | 8128 | 9105 | 2026cycle | `/donors/key/jb%7Cpritzker%7C111%20s%20wacker%20dr%7Cste%204000%7Cchicago%7Cil%7C60606-4309?period=2026cycle` |  |
| 200 | 7705 | 28829 | all | `/?period=all` |  |
| 200 | 7392 | 9183 | 2026cycle | `/about?period=2026cycle` |  |
| 200 | 6161 | 72637 | 2026cycle | `/donors/?period=2026cycle` |  |
| 200 | 5995 | 13005 | 2026cycle | `/analytics/state-races/governor-statewide-illinois-f72f709631?period=2026cycle` |  |
| 200 | 5776 | 200000 | 2026cycle | `/live-feed?period=2026cycle` |  |
| 200 | 5599 | 21368 | 2026cycle | `/lobbying/?period=2026cycle` |  |
| 200 | 5495 | 28766 | all | `/analytics/overview?period=all` |  |
| 200 | 5269 | 13367 | 2026cycle | `/lobbying/client/2?period=2026cycle` |  |
| 200 | 4920 | 12229 | 2026cycle | `/527/364367949?period=2026cycle` |  |
| 200 | 4751 | 28971 | 2026cycle | `/?period=2026cycle` |  |
| 200 | 4474 | 21254 | all | `/lobbying/?period=all` |  |
| 200 | 4326 | 28285 | 2026cycle | `/analytics/overview?period=2026cycle` |  |
| 200 | 4245 | 8169 | 2026cycle | `/admin/donor-merges?period=2026cycle` |  |
| 200 | 4020 | 8169 | all | `/admin/donor-merges?period=all` |  |
| 200 | 3974 | 67203 | all | `/compare?period=all` |  |
| 200 | 3893 | 9069 | all | `/about?period=all` |  |
| 200 | 3635 | 67317 | 2026cycle | `/compare?period=2026cycle` |  |
| 200 | 3369 | 200000 | 2026cycle | `/federal-finance/networks?period=2026cycle` |  |
| 200 | 3356 | 8157 | 2026cycle | `/admin/?period=2026cycle` |  |
| 200 | 3297 | 32718 | all | `/reports/?period=all` |  |
| 200 | 3284 | 75073 | 2026cycle | `/federal-finance/donor-intelligence?period=2026cycle` |  |
| 200 | 3179 | 82094 | 2026cycle | `/federal-finance/money-flow?period=2026cycle` |  |
| 200 | 3163 | 20450 | all | `/lobbying/client/2?period=all` |  |
| 200 | 3122 | 200000 | 2026cycle | `/analytics/networks?period=2026cycle` |  |
| 200 | 3110 | 8050 | all | `/legacy?period=all` |  |
| 200 | 2852 | 185355 | all | `/federal-finance/H0IL05096?period=all` |  |
| 200 | 2745 | 36506 | all | `/federal-finance/?period=all` |  |
| 200 | 2663 | 185469 | 2026cycle | `/federal-finance/H0IL05096?period=2026cycle` |  |
| 200 | 2662 | 12115 | all | `/527/364367949?period=all` |  |
| 200 | 2504 | 36884 | 2026cycle | `/federal-finance/?period=2026cycle` |  |
| 200 | 2485 | 81980 | all | `/federal-finance/money-flow?period=all` |  |
| 200 | 2471 | 46193 | all | `/federal-finance/candidates?period=all` |  |
| 200 | 2439 | 200000 | all | `/analytics/networks?period=all` |  |
| 200 | 2398 | 60661 | 2026cycle | `/federal-finance/follow-the-money?period=2026cycle` |  |
| 200 | 2346 | 74959 | all | `/federal-finance/donor-intelligence?period=all` |  |
| 200 | 2276 | 28277 | 2026cycle | `/analytics/?period=2026cycle` |  |
| 200 | 2270 | 36884 | 2026cycle | `/federal-finance/overview?period=2026cycle` |  |
| 200 | 2211 | 8178 | all | `/admin/federal-receipt-audit?period=all` |  |
| 200 | 2207 | 11235 | all | `/lobbying/flows?period=all` |  |
| 200 | 2180 | 8299 | 2026cycle | `/committees/filing/1?period=2026cycle` |  |
| 200 | 2177 | 28537 | 2026cycle | `/527/?period=2026cycle` |  |
| 200 | 2144 | 7865 | all | `/person-intelligence?period=all` |  |
| 200 | 2108 | 9359 | 2026cycle | `/experimental/viz-lab/state-maps?period=2026cycle` |  |
| 200 | 2093 | 28558 | all | `/openbook/?period=all` |  |
| 200 | 2040 | 44765 | 2026cycle | `/analytics/geography?period=2026cycle` |  |
| 200 | 1998 | 10598 | all | `/lobbying/2?period=all` |  |
| 200 | 1934 | 200000 | all | `/federal-finance/networks?period=all` |  |
| 200 | 1933 | 13362 | all | `/investigate?period=all` |  |
| 200 | 1910 | 21308 | 2026cycle | `/analytics/donors?period=2026cycle` |  |
| 200 | 1825 | 13476 | 2026cycle | `/investigate?period=2026cycle` |  |
| 200 | 1812 | 34764 | 2026cycle | `/527/dark-money?period=2026cycle` |  |
| 200 | 1707 | 35775 | 2026cycle | `/federal-finance/geography?period=2026cycle` |  |
| 200 | 1626 | 31510 | all | `/federal-finance/influence?period=all` |  |
| 200 | 1586 | 10712 | 2026cycle | `/lobbying/2?period=2026cycle` |  |
| 200 | 1515 | 77103 | all | `/analytics/risk?period=all` |  |
| 200 | 1491 | 9142 | all | `/donors/key/jb%7Cpritzker%7C111%20s%20wacker%20dr%7Cste%204000%7Cchicago%7Cil%7C60606-4309?period=all` |  |
| 200 | 1477 | 36506 | all | `/federal-finance/overview?period=all` |  |
| 200 | 1398 | 8050 | all | `/auth/login?period=all` |  |
| 200 | 1394 | 16081 | all | `/federal-finance/matches/nsz_a590d94a11f0607ce9af/don%7Ctracy%7C1429%20e%20lake%20shore%20dr%7C%7Cspringfield%7Cil%7C62712?period=all` |  |
| 200 | 1393 | 29035 | all | `/federal-finance/donors/nsz_0005e68b50de5fe1321c?period=all` |  |
| 200 | 1377 | 26747 | 2026cycle | `/federal-finance/races/H/01/outside-spending?period=2026cycle` |  |
| 200 | 1358 | 31624 | 2026cycle | `/federal-finance/influence?period=2026cycle` |  |
| 200 | 1304 | 35121 | all | `/federal-finance/geography?period=all` |  |
| 200 | 1158 | 32395 | 2026cycle | `/d2-reconciliation/?period=2026cycle` |  |
| 200 | 1156 | 8164 | 2026cycle | `/auth/login?period=2026cycle` |  |
| 200 | 1121 | 26633 | all | `/federal-finance/races/H/01/outside-spending?period=all` |  |
| 200 | 1095 | 8185 | all | `/committees/filing/1?period=all` |  |
| 200 | 1084 | 46307 | 2026cycle | `/federal-finance/candidates?period=2026cycle` |  |
| 200 | 1066 | 9239 | all | `/experimental/viz-lab/state-maps?period=all` |  |
| 200 | 1066 | 83677 | all | `/candidate-finance/39266/32762/itemized-expenditures?period=all` |  |
| 200 | 1028 | 8164 | 2026cycle | `/legacy?period=2026cycle` |  |
| 200 | 1003 | 11928 | all | `/openbook/AMEREN%20CIPS?period=all` |  |
| 200 | 990 | 200000 | 2026cycle | `/analytics/relationships?period=2026cycle` |  |
| 200 | 978 | 8164 | all | `/manual-entry/?period=all` |  |
| 200 | 946 | 8157 | all | `/admin/?period=all` |  |
| 200 | 889 | 8183 | all | `/admin/federal-disbursement-audit?period=all` |  |
| 200 | 862 | 31546 | all | `/d2-reconciliation/?period=all` |  |
| 200 | 851 | 7979 | 2026cycle | `/person-intelligence?period=2026cycle` |  |
| 200 | 840 | 11349 | 2026cycle | `/lobbying/flows?period=2026cycle` |  |
| 200 | 827 | 83789 | 2026cycle | `/candidate-finance/39266/32762/itemized-expenditures?period=2026cycle` |  |
| 200 | 815 | 16195 | 2026cycle | `/federal-finance/matches/nsz_a590d94a11f0607ce9af/don%7Ctracy%7C1429%20e%20lake%20shore%20dr%7C%7Cspringfield%7Cil%7C62712?period=2026cycle` |  |
| 200 | 794 | 10705 | all | `/committees/sbe/1?period=all` |  |
| 200 | 786 | 12042 | 2026cycle | `/openbook/AMEREN%20CIPS?period=2026cycle` |  |
| 200 | 785 | 29149 | 2026cycle | `/federal-finance/donors/nsz_0005e68b50de5fe1321c?period=2026cycle` |  |
| 200 | 771 | 28758 | all | `/analytics/?period=all` |  |
| 200 | 770 | 8183 | 2026cycle | `/admin/federal-disbursement-audit?period=2026cycle` |  |
| 200 | 765 | 10823 | 2026cycle | `/committees/sbe/1?period=2026cycle` |  |
| 200 | 721 | 8178 | 2026cycle | `/admin/federal-receipt-audit?period=2026cycle` |  |
| 200 | 711 | 32832 | 2026cycle | `/reports/?period=2026cycle` |  |
| 200 | 701 | 8164 | 2026cycle | `/manual-entry/?period=2026cycle` |  |
| 200 | 696 | 8011 | all | `/search?period=all` |  |
| 200 | 688 | 8125 | 2026cycle | `/search?period=2026cycle` |  |
| 200 | 682 | 60547 | all | `/federal-finance/follow-the-money?period=all` |  |
| 200 | 681 | 28671 | 2026cycle | `/openbook/?period=2026cycle` |  |
| 200 | 661 | 13258 | all | `/committees/1?period=all` |  |
| 200 | 634 | 25393 | 2026cycle | `/committees/?period=2026cycle` |  |
| 200 | 629 | 200000 | all | `/analytics/relationships?period=all` |  |
| 200 | 623 | 67195 | all | `/donors/?period=all` |  |
| 200 | 621 | 79244 | 2026cycle | `/analytics/risk?period=2026cycle` |  |
| 200 | 575 | 25279 | all | `/committees/?period=all` |  |
| 200 | 547 | 82272 | all | `/candidate-finance/39266/32762/itemized?period=all` |  |
| 200 | 545 | 11953 | 2026cycle | `/committees/1?period=2026cycle` |  |
| 200 | 536 | 50948 | 2026cycle | `/candidate-finance/39266/32762/itemized?period=2026cycle` |  |
| 200 | 476 | 10246 | all | `/candidates?period=all` |  |
| 200 | 462 | 10356 | 2026cycle | `/candidates?period=2026cycle` |  |
| 200 | 102 | 917 | 2026cycle | `/experimental/viz-lab/data/geometry/il/status?period=2026cycle` |  |
| 200 | 101 | 29 | 2026cycle | `/experimental/viz-lab/data/triple-pipeline/timeline?period=2026cycle` |  |
| 200 | 100 | 29 | all | `/experimental/viz-lab/data/triple-pipeline/timeline?period=all` |  |
| 200 | 98 | 917 | all | `/experimental/viz-lab/data/geometry/il/status?period=all` |  |

## auth-gated (44)

_401s on `/api/*` are expected — the sweep does not authenticate. These are listed for completeness, not as breakage. See commit `c94ad2f` for the API auth gate._

| Status | ms | Bytes | Period | URL | Markers |
|---:|---:|---:|---|---|---|
| 401 | 5752 | 29 | 2026cycle | `/api/analytics/irs527-ecosystem?period=2026cycle` |  |
| 401 | 3758 | 29 | 2026cycle | `/api/committees?period=2026cycle` |  |
| 401 | 1957 | 29 | 2026cycle | `/api/donors?period=2026cycle` |  |
| 401 | 1156 | 29 | all | `/api/stats?period=all` |  |
| 401 | 780 | 29 | all | `/api/scrape-status?period=all` |  |
| 401 | 750 | 29 | 2026cycle | `/api/stats?period=2026cycle` |  |
| 401 | 177 | 29 | 2026cycle | `/api/analytics/lobbying-influence?period=2026cycle` |  |
| 401 | 114 | 29 | 2026cycle | `/api/analytics/reconciliation?period=2026cycle` |  |
| 401 | 106 | 29 | all | `/api/analytics/concentration?period=all` |  |
| 401 | 106 | 29 | all | `/api/federal/network/suggest?period=all` |  |
| 401 | 106 | 29 | all | `/api/analytics/donor-cogiving?period=all` |  |
| 401 | 105 | 29 | all | `/api/analytics/network/focus-sankey?period=all` |  |
| 401 | 104 | 29 | all | `/api/donors?period=all` |  |
| 401 | 103 | 29 | 2026cycle | `/api/federal/network/suggest?period=2026cycle` |  |
| 401 | 103 | 29 | all | `/api/analytics/network?period=all` |  |
| 401 | 103 | 29 | all | `/api/analytics/committee-similarity?period=all` |  |
| 401 | 102 | 29 | all | `/api/analytics/network/suggest?period=all` |  |
| 401 | 101 | 29 | all | `/api/federal/network/focus-sankey?period=all` |  |
| 401 | 101 | 29 | all | `/api/analytics/nlp?period=all` |  |
| 401 | 100 | 29 | 2026cycle | `/api/federal/network/focus-sankey?period=2026cycle` |  |
| 401 | 100 | 29 | 2026cycle | `/api/analytics/network?period=2026cycle` |  |
| 401 | 100 | 29 | 2026cycle | `/api/analytics/candidate-competition?period=2026cycle` |  |
| 401 | 99 | 29 | 2026cycle | `/api/reports?period=2026cycle` |  |
| 401 | 99 | 29 | 2026cycle | `/api/scrape-status?period=2026cycle` |  |
| 401 | 99 | 29 | 2026cycle | `/api/analytics/geo?period=2026cycle` |  |
| 401 | 99 | 29 | 2026cycle | `/api/analytics/nlp?period=2026cycle` |  |
| 401 | 99 | 29 | all | `/api/analytics/time-series?period=all` |  |
| 401 | 98 | 29 | all | `/api/analytics/geo?period=all` |  |
| 401 | 98 | 29 | 2026cycle | `/api/analytics/committee-similarity?period=2026cycle` |  |
| 401 | 96 | 29 | all | `/api/analytics/anomalies?period=all` |  |
| 401 | 96 | 29 | all | `/api/analytics/reconciliation?period=all` |  |
| 401 | 96 | 29 | 2026cycle | `/api/analytics/donor-cogiving?period=2026cycle` |  |
| 401 | 95 | 29 | 2026cycle | `/api/analytics/network/suggest?period=2026cycle` |  |
| 401 | 95 | 29 | 2026cycle | `/api/analytics/concentration?period=2026cycle` |  |
| 401 | 94 | 29 | 2026cycle | `/api/committees/1?period=2026cycle` |  |
| 401 | 94 | 29 | 2026cycle | `/api/analytics/network/focus-sankey?period=2026cycle` |  |
| 401 | 92 | 29 | all | `/api/analytics/irs527-ecosystem?period=all` |  |
| 401 | 92 | 29 | all | `/api/committees/1?period=all` |  |
| 401 | 92 | 29 | 2026cycle | `/api/analytics/time-series?period=2026cycle` |  |
| 401 | 92 | 29 | all | `/api/analytics/lobbying-influence?period=all` |  |
| 401 | 91 | 29 | all | `/api/analytics/candidate-competition?period=all` |  |
| 401 | 89 | 29 | 2026cycle | `/api/analytics/anomalies?period=2026cycle` |  |
| 401 | 87 | 29 | all | `/api/committees?period=all` |  |
| 401 | 86 | 29 | all | `/api/reports?period=all` |  |

## skipped (14)

| Rule | Endpoint | Reason |
|---|---|---|
| `/admin/donor-merges/<path:entity_id>` | admin.donor_merge_detail | no seed available |
| `/api/donors/<int:donor_id>` | api.get_donor | no seed available |
| `/api/reports/<int:report_id>` | api.get_report | no seed available |
| `/donors/<int:donor_id>` | donors.donor_detail | no seed available |
| `/donors/entity/<path:entity_id>` | donors.donor_detail_by_entity | no seed available |
| `/manual-entry/<int:queue_id>` | manual_entry.entry_form | no seed available |
| `/reports/<int:report_id>` | reports.report_detail | no seed available |

## Period-filter no-op detector

Routes where the payload size is identical across `?period=2026cycle` and `?period=all`
(suggests the route does not actually filter by period):

| URL | Bytes |
|---|---:|
| `/527/suggest` | 3 |
| `/admin/` | 8157 |
| `/admin/donor-merges` | 8169 |
| `/admin/federal-disbursement-audit` | 8183 |
| `/admin/federal-receipt-audit` | 8178 |
| `/analytics/networks` | 200000 |
| `/analytics/relationships` | 200000 |
| `/auth/logout` | 28971 |
| `/committees/suggest` | 3 |
| `/experimental/viz-lab/data/geometry/il/status` | 917 |
| `/experimental/viz-lab/data/triple-pipeline/timeline` | 29 |
| `/federal-finance/matching` | 200000 |
| `/federal-finance/networks` | 200000 |
| `/live-feed` | 200000 |
| `/lobbying/flows/suggest` | 3 |
| `/lobbying/suggest` | 3 |
| `/manual-entry/` | 8164 |

