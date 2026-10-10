
#  How I used AI (Cursor, Grok 4.7)


##  What the assistant did
  

An AI coding assistant in Cursor (Grok 4.7) was used throughout the whole project.

I started off with telling the assistant I want to scrape data and that I wanted to scrape Steam and SteamDB but SteamDB was not working so I used the alternative SteamCharts. Steam supplied most the information of the game like the game id, titles, dates, genres, categories, reviews and Metacritic scores.

The assistant then wrote the python code to create the database and to scrape from Steam and SteamCharts. The scraping then could be accomplished with the command:
 `
.venv\Scripts\python -m src.collect --per-year 300
`
300 in the command means 300 games per year are scraped from January 2018 to March 31, 2026. March is picked because we had to account for 6 months of reviews later, versus if today was the cutoff then we would not have reviews for in the future.

I also asked AI a good statistic that can be used to answer the question and it recommended using log retention with the spearman correlation.

 
  

##  Difficulties

- Steam search returned HTTP 429 when pages were requested too quickly. The collector now waits between requests and waits longer after a 429. That made the search phase slow. Proxies were considered as a way around the limit and were not used. (I ended up running the scraper overnight as it took around 4 hours to scrape)

- A first sample took games from the end of each year. Most had no SteamCharts history, so only 7 of 108 games were usable. The collector was rewritten to spread pages across each year and to keep the most-reviewed game on a page when it has at least 1,000 lifetime reviews.

- Some SteamCharts app pages return HTTP 500. Retrying those for a long time stalled the run. A repeated 500 is now recorded as a missing chart.

- The review summary had to be checked against a known game before trusting it. Lifetime scores would have mixed in reviews written after players had already stayed or left.

- Metacritic scores are missing for 1,375 of the 1,822 included games, so question 1's shared sample is 447 games. The early-player association in that sample does not appear in the full 1,822.

- Question 2's multiplayer only group has 163 games. Many games are tagged both single and multiplayer and were left out of that comparison.

- I was confused at the whole spearman correlation at first but after asking AI about it and comparing it with online resources I then understood how it worked to help me with answering my questions.