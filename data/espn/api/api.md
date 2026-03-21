# API

This is ESPN's undocumented public API. The app should only query it sparingly.

## Scores
Querying `https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=100&seasontype=3&dates=20260319-20260407` gives output like `data/espn/scoreboard.json`

The example data in `data/espn/scoreboard.json` contains all the kinds of games we are likely to find: Completed games, in progress games, future games where both teams have been determined, future games where one team has been determined, and future games where no teams have been determined.

## Teams
Querying `https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams` gives output like `data/espn/teams.json`

The data we care about here (team names, UIDs, logos, etc.) are unlikely to change.