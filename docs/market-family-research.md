# Market Family Research

## Scope

This memo ranks Polymarket market families by their suitability for an automated research-and-trading system.

Important:

- This is not a claim of guaranteed profit.
- The ranking is an inference from source quality, automation fit, crowding, and current Polymarket supply.
- For financial risk reasons, every family below should remain `dry_run` until it has enough evidence and post-run review data.

## Selection Rubric

I ranked families using five practical criteria:

1. Primary-source quality: Is there one authoritative source, or at least a narrow source set?
2. Automation fit: Can the data be fetched and normalized by a small Python agent?
3. Repeatability: Does the family recur often enough to learn from history?
4. Edge potential: Is there a plausible path to a real informational edge rather than just copying consensus?
5. Market availability: Does Polymarket actually list enough of these markets to matter?

## Live Polymarket Snapshot

Inference from a live Gamma API snapshot taken on March 20, 2026:

- `GET /markets?limit=1000&active=true&closed=false` returned 500 active markets.
- A simple keyword-based classification of those 500 active markets found:
  - sports: 203 markets, about 9.95M 24h volume
  - politics: 182 markets, about 12.66M 24h volume
  - legal/regulatory: 7 markets, about 14.5k 24h volume
  - crypto: 4 markets, about 24.1k 24h volume
  - misc/pop-culture/geopolitical: 104 markets, about 1.69M 24h volume

This is only a heuristic snapshot, not a full Polymarket taxonomy. It is useful as evidence of current surface area and liquidity concentration.

## Ranked Families

### 1. Regulatory and agency decision markets

Why chosen:

- Best chance of a real informational edge from primary-document reading.
- Official sources are high quality and often machine-readable.
- Many traders react to headlines; fewer parse filings, calendars, briefing materials, and procedural deadlines in detail.

Why the edge may exist:

- The SEC says `data.sec.gov` APIs provide JSON access to submissions and are updated throughout the day in real time, with the submissions API typically updated with less than a second of processing delay.
- FDA advisory committee meetings are announced publicly and at least 15 calendar days in advance, and openFDA exposes structured API access with free API keys and generous limits.
- That creates a path for an agent to monitor deadlines, filings, briefing documents, and meeting outcomes faster and more consistently than casual traders.

Main strengths:

- Excellent primary-source discipline
- Strong fit for LLM-assisted document reading
- Often clearer causal milestones than narrative markets

Main risks:

- Lower market frequency than sports
- Some court data is locked behind PACER or mixed with secondary reporting
- Resolution language can be legally precise and easy to misread

Verdict:

- Highest potential alpha family of the set
- Not the easiest first build, but the best niche to target if the goal is genuine informational edge

### 2. Macroeconomic release markets

Why chosen:

- Cleanest first systematic family for a serious trading workflow.
- Official release calendars are explicit.
- Historical data is deep.
- Forecasting can be backtested.

Why the edge may exist:

- BLS publishes exact release times for CPI and employment data.
- BEA publishes an official release schedule with exact timestamps.
- The Atlanta Fed's GDPNow produces a public, model-based nowcast from official data using a fixed methodology and no judgmental adjustments.
- A trading agent can compare market odds to consensus, nowcasts, revision risk, and surprise distributions before a release.

Main strengths:

- Very objective resolution
- Excellent historical backtesting path
- Strong habit formation for the repo: schedule, fetch, score, review

Main risks:

- Highly competitive around release time
- Some edge requires a better model, not just faster reading
- Release-time latency matters if the system tries to trade after the print

Verdict:

- Best first implementation family
- Slightly lower alpha potential than regulatory/agency markets, but much better for building a disciplined MVP

### 3. Weather and natural disaster markets

Why chosen:

- NOAA and NWS provide unusually strong official forecast infrastructure for an automated agent.
- Forecast uncertainty is already expressed probabilistically through ensemble systems.
- When Polymarket lists these markets, the data-to-decision path is much cleaner than in celebrity or rumor markets.

Why the edge may exist:

- The National Weather Service documents machine-readable API services, including alerts.
- NOAA's ensemble forecast systems are explicitly built to quantify uncertainty across multiple forecast paths.
- Recent NOAA material says the AI-enhanced AIGEFS and HGEFS products improved forecast skill versus traditional ensemble systems.
- NHC verification reports show forecast skill and error are measured and published, which is exactly what an agent needs for calibration.

Main strengths:

- Strong probability-native source data
- Clear geographic and temporal framing
- Good fit for threshold markets

Main risks:

- Availability on Polymarket is episodic
- Market wording can depend on exact station, basin, or threshold definitions
- Liquidity may be thinner than sports or politics

Verdict:

- Strong family whenever available
- Better than sports for pure evidence quality, but worse on market supply

### 4. Sports markets with official data

Why chosen:

- Sports is the deepest currently active family on Polymarket outside politics.
- There is enough market count and liquidity to train the workflow and produce many reports quickly.
- Official data is abundant for schedules, standings, injuries, and suspensions.

Why the edge may still be hard:

- In the March 20, 2026 active snapshot, sports was the single largest clearly structured family after politics, with 203 heuristic matches and about 9.95M in 24h volume.
- The NBA's official injury report requires teams to report player availability and states that reports are updated continually throughout the day.
- But academic evidence cuts against easy profit here: Steven Levitt's NBER paper on NFL betting found little evidence bettors systematically beat bookmakers.

Main strengths:

- High throughput
- Clean official league data
- Easy to generate repeated dry-run decisions

Main risks:

- Likely the most efficient family of the four
- Public benchmark prices from sportsbooks are strong competition
- Many apparent edges disappear after vig, stale data, or injury-news race conditions

Verdict:

- Worth supporting because Polymarket lists many of these markets now
- Not the first family I would target if the objective is extractable alpha rather than operational reps

## Families Not Chosen First

### Politics and elections

Why not first:

- Plenty of liquidity, but also the most crowded and model-saturated family.
- NBER work in 2025 explicitly combines polling data, economic fundamentals, and prediction market prices to forecast U.S. presidential outcomes.
- That is useful evidence that political markets are information-rich, but it also means a small MVP is stepping into a very competitive information stack.

Verdict:

- Good to support later
- Poor first target for a bare-bones system that still needs to prove it can form an edge

### Pop culture, celebrity, and novelty markets

Why not first:

- Weak primary-source discipline
- Heavy rumor content
- Resolution language often depends on ambiguous public statements or media interpretation

Verdict:

- Avoid early

### Long-horizon outright winner markets

Why not first:

- Capital stays tied up for long periods
- Thesis drift is constant
- A bare-bones system benefits more from shorter feedback loops

Verdict:

- Better later, after history and calibration are stronger

## Recommended Build Order

If the goal is highest likely edge:

1. Regulatory and agency decisions
2. Macroeconomic releases
3. Weather and natural disaster
4. Sports

If the goal is easiest disciplined MVP with repeatable backtests:

1. Macroeconomic releases
2. Regulatory and agency decisions
3. Sports
4. Weather and natural disaster

## Recommendation For This Repo

My recommendation is:

1. Build `macroeconomic releases` first as the operational MVP.
2. Build `regulatory and agency decisions` second as the first real alpha hunt.
3. Add `sports` after the workflow is stable, mostly for repetition and market coverage.
4. Add `weather` opportunistically when relevant markets appear.

That sequencing gives you a clean first implementation without losing sight of the higher-upside niche.

## Sources

- Polymarket market-data overview: <https://docs.polymarket.com/market-data/overview>
- SEC EDGAR APIs: <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
- SEC EDGAR search and access: <https://www.sec.gov/edgar/search-and-access>
- openFDA APIs: <https://open.fda.gov/apis/>
- openFDA authentication and limits: <https://open.fda.gov/apis/authentication/>
- FDA advisory committee calendar: <https://www.fda.gov/advisory-committees/advisory-committee-calendar>
- FDA advisory committee FAQ: <https://www.fda.gov/advisory-committees/about-advisory-committees/common-questions-and-answers-about-fda-advisory-committee-meetings>
- BLS CPI release schedule: <https://www.bls.gov/schedule/news_release/cpi.htm>
- BLS release calendar: <https://www.bls.gov/schedule/news_release/>
- BLS CPI home: <https://www.bls.gov/cpi/>
- BEA release schedule: <https://www.bea.gov/news/schedule>
- Atlanta Fed GDPNow page: <https://www.atlantafed.org/research-and-data/data/gdpnow>
- NWS API documentation: <https://www.weather.gov/documentation/services-web-api>
- NWS alerts API documentation: <https://www.weather.gov/documentation/services-web-alerts>
- NOAA GEFS overview: <https://www.ncei.noaa.gov/index.php/products/weather-climate-models/global-ensemble-forecast>
- NOAA DESI overview: <https://gsl.noaa.gov/desi>
- NOAA AI weather model update: <https://gsl.noaa.gov/news/new-ai-weather-forecast-models-added-to-desi>
- NHC verification reports: <https://www.nhc.noaa.gov/verification/>
- NBA injury report: <https://official.nba.com/nba-injury-report-2025-26-season/>
- FIFA World Cup 26 official coverage: <https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026>
- NBER on sportsbook efficiency: <https://www.nber.org/papers/w9422>
- NBER on election prediction markets and polls: <https://www.nber.org/papers/w33339>
