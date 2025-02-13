Release Notes

* Removed dependency on Komga for path - full UI support for browsing you comic directories and selecting folders or files
    * If a file is selected via UI, you will be dropped into the same directory when you click Browse
    * Validation added to dis-allow running of functions on your entire library
* Updated UI to prevent user from running another process while one is running
* Update to web based config settings, except for folder monitoring
* Added header navigation
* Added log monitoring in app
* Updated folder monitoring to auto-convert to CBZ after renaming (if enabled in web UI)
* Updated folder monitoring to traverse sub-ddirectories (if enabled in web ui)
* Backend: migrated tempates and pages to Jinja2
* Backend: consolidate config loading to ensure values updated via web are refreshed
* Backend: colsolidate logging to single file
* Bug Fix: Status messages could time out on long processes