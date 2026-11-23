---
title: Synthetic lake monitoring report
status: draft
---

# Synthetic lake monitoring report

## Question

Did dissolved oxygen change across six synthetic monitoring sites from 2020 to 2024?

## Data and provenance

**Table 1. Data sources used in this report.** Year is the observation period.

| Dataset | Year | Data type / resolution | Source |
|---|---|---|---|
| Monthly oxygen series (`monthly-oxygen`) | 2020–2024 | Synthetic monthly values, six sites | [Fixture generator](../scripts/lake_visuals.py) |
| Paired oxygen values (`paired-oxygen`) | Not applicable | Simulated observed and modeled pairs | [Fixture generator](../scripts/lake_visuals.py) |

## Methods

[UNRESOLVED: methodology diagram selection] Choose an option before inserting a diagram.
Rebuild the options with `python scripts/lake_visuals.py prototypes` once the report figures exist.

The generator simulates monthly oxygen series with a linear trend, seasonal cycle, and noise.
It fits a harmonic regression per site and reports approximate 95% intervals as half-widths
in the trend table. For a separate illustration of agreement, it simulates paired values by
adding bias and noise to randomly drawn oxygen values.

## Main results

![Monthly oxygen series for two sites and decadal trends for six sites](figures/lake/trends.png)

**Figure 2. Oxygen trends differ across monitoring sites.** (a) Monthly series for two sites. (b) Decadal trends with 95% confidence intervals, highest at North Bay at [1.85](../evidence/lake/metrics.json#/top_trend) mg/L per decade.

![Observed and modeled oxygen scatter plot with a one-to-one line](figures/lake/validation.png)

**Figure 3. Simulated modeled and observed oxygen values are close to the one-to-one line.** RMSE was [0.33](../evidence/lake/metrics.json#/rmse) mg/L across [140](../evidence/lake/metrics.json#/n_validation) samples.

The full site results are in [Table 2](tables/lake/site_trends.md).

## Limitations

All values are synthetic and support no scientific claim.

## Appendix: source provenance

**Table A1. Provenance of each data source.** Record counts link to evidence.

| Source ID | Observed | Released | Source resolution | Analysis or display resolution | Records | Identity | Open gap |
|---|---|---|---|---|---|---|---|
| `monthly-oxygen` | 2020–2024 (simulated) | Not applicable | Monthly by site | Same | [360](../evidence/lake/sources.json#/monthly-oxygen) | Synthetic | None |
| `paired-oxygen` | Not applicable | Not applicable | One simulated pair per draw | Same | [140](../evidence/lake/sources.json#/paired-oxygen) | Synthetic | None |
