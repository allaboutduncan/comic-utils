import os
import json
import logging
from config import config

# Try imports, logging warning if missing since these are optional dependencies until now
try:
    import openai
except ImportError:
    openai = None

try:
    import anthropic
except ImportError:
    anthropic = None

# Set up logging
logger = logging.getLogger(__name__)

def get_recommendations(api_key, provider, model, reading_history):
    """
    Get comic recommendations based on reading history using the specified LLM provider.
    
    Args:
        api_key (str): API Key for the provider
        provider (str): 'gemini', 'openai', or 'anthropic'
        model (str): Specific model name to use
        reading_history (list): List of titles/series recently read
        
    Returns:
        list: List of recommendation dictionaries
    """
    
    if not api_key:
        logger.error("No API key provided for recommendations")
        return {"error": "API Key is required"}
        
    if not reading_history:
        return []
        
    # Check for library availability
    if (provider == 'gemini' or provider == 'openai') and openai is None:
        return {"error": "The 'openai' python package is required. Please run: pip install openai"}
    if provider == 'anthropic' and anthropic is None:
        return {"error": "The 'anthropic' python package is required. Please run: pip install anthropic"}

    # Format the prompt
    history_text = "\n".join([f"- {item['series']} ({item['title']})" for item in reading_history])
    
    system_prompt = (
        "You are an expert comic book librarian with deep knowledge of comics, graphic novels, and manga. "
        "Based on the user's recent reading history provided below, suggest 5 new series they might enjoy. "
        "Focus on finding hidden gems, critically acclaimed runs, or similar tones/themes, avoid suggesting the exact same series they just read. "
        "\n\n"
        "Return the response ONLY as a valid JSON array of objects with these keys:\n"
        "- title: The title of the series\n"
        "- publisher: The publisher (e.g., Image, Marvel, DC, Fantagraphics)\n"
        "- reason: A brief, persuasive reason why they'll like it based on their history (1-2 sentences)\n"
        "- volume: (Optional) A specific starting volume or 'Vol 1' if applicable\n"
    )
    
    user_prompt = f"Here is my recent reading history:\n{history_text}\n\nSuggest 5 recommendations in JSON format."

    try:
        if provider == "gemini":
            return _call_gemini_via_openai(api_key, model, system_prompt, user_prompt)
        elif provider == "openai":
            return _call_openai(api_key, model, system_prompt, user_prompt)
        elif provider == "anthropic":
            return _call_anthropic(api_key, model, system_prompt, user_prompt)
        else:
            return {"error": f"Unsupported provider: {provider}"}
            
    except Exception as e:
        logger.error(f"Error getting recommendations from {provider}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": f"API Error: {str(e)}"}

def _parse_json_response(content):
    """Helper to safely parse JSON from LLM response"""
    try:
        # Clean up markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[0].strip() # Fallback
            
        return json.loads(content)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON response: {content}")
        return {"error": "Failed to parse recommendations from AI response"}

def _call_gemini_via_openai(api_key, model, system_prompt, user_prompt):
    # Gemini supports OpenAI-compatible library calls
    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    
    # Gemini via OpenAI compat works best with a single message or mapped roles
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"} # Try to enforce JSON mode
    )
    
    content = response.choices[0].message.content
    return _parse_json_response(content)

def _call_openai(api_key, model, system_prompt, user_prompt):
    client = openai.OpenAI(api_key=api_key)
    
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"}
    )
    
    content = response.choices[0].message.content
    
    # OpenAI json_object mode requires the output to be a valid JSON object, 
    # but our prompt asks for an array. Sometimes wrapper objects are returned.
    parsed = _parse_json_response(content)
    
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        # Check for common wrapper keys like 'recommendations' or 'series'
        for key in ['recommendations', 'series', 'suggestions']:
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        # customized fallback
        return [parsed] 
        
    return parsed

def _call_anthropic(api_key, model, system_prompt, user_prompt):
    client = anthropic.Anthropic(api_key=api_key)
    
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )
    
    content = message.content[0].text
    return _parse_json_response(content)