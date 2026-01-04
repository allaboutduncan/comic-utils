# Recommendations

## Core Architecture
You can build a unified recommendation service that formats user data into a prompt and routes it to the provider of their choice.

### Data Preparation
Our sqlite database tracks what a user has read. This is stored in the `issues_read` table referenced in database.py

Series Titles are contained in .issue_path'

## Recommendation Flow
Frontend: User enters their API key in "Settings" and selects a model (Gemini, GPT-4o, or Claude 3.5).

Backend: Flask fetches the user’s reading list (Last 200 issues in issues_read).

LLM Service: A Python function constructs a prompt and calls the respective API. The framework for this is in recommendations.py

The Prompt: "You are an expert comic book librarian. Based on the following reading history: [List of Titles], suggest 5 new series. For each suggestion, provide the Title, Publisher, and a 'Why you'll like it' reason based on their specific tastes. Return the data in valid JSON format."

Parsing: The LLM returns a JSON list of recommendations, which CLU displays as a "Discover" feed.

Display: Create a HTML page that displays the recommendations in a clean, modern way. We'll embed this on the collection.html page for now - under the pagination. We should have a "Refresh Recommendations" button that calls the LLM again once use has read additional issues