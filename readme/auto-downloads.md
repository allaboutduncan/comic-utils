● Implementation complete. Here's a summary of all changes:

  1. database.py
  - Added getcomics_schedule table (lines 138-156)
  - Added get_getcomics_schedule() function (lines 1355-1384)
  - Added save_getcomics_schedule() function (lines 1387-1419)
  - Added update_last_getcomics_run() function (lines 1422-1449)

  2. models/getcomics.py
  - Added score_getcomics_result() function (lines 146-199) with scoring:
    - Series name match (fuzzy): +40 points
    - Issue number match: +40 points
    - Year match: +20 points
    - Threshold: 60 points minimum to queue download

  3. app.py
  - Added getcomics_scheduler initialization (lines 231-234)
  - Added scheduled_getcomics_download() task function (lines 595-736) that:
    - Gets all wanted issues with store_date >= today
    - Searches GetComics for each issue
    - Scores results and queues downloads for matches >= 60
  - Added configure_getcomics_schedule() function (lines 739-789)
  - Added startup call to configure_getcomics_schedule() (line 10432)
  - Added API endpoints:
    - GET /api/get-getcomics-schedule (lines 3398-3441)
    - POST /api/save-getcomics-schedule (lines 3444-3482)
    - POST /api/run-getcomics-now (lines 3485-3497)

  4. templates/config.html
  - Added GetComics Auto-Download UI section (lines 714-775) with:
    - Frequency dropdown (disabled/daily/weekly)
    - Time picker
    - Weekday selector (for weekly)
    - Save and "Download Now" buttons
    - Last run / Next run display
  - Added JavaScript functions (lines 1459-1563):
    - loadGetcomicsSchedule()
    - saveGetcomicsSchedule()
    - runGetcomicsNow()
  - Added event listener for weekday toggle (lines 1210-1218)
  - Added onload call (lines 1223-1224)