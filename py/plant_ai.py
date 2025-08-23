"""
Simplified AI-powered plant information service.
1. Search Boutique Végétale
2. Extract with ministral-8b-latest 
3. Fallback to mistral-large-latest
"""

import json
import logging
import re
import urllib.parse
from typing import Dict, Any, Optional

try:
    import requests
    from bs4 import BeautifulSoup
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False

try:
    from mistralai import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False


class PlantAIService:
    """Simplified plant information service."""
    
    def __init__(self, small_model_key: str, large_model_key: str):
        self.logger = logging.getLogger(__name__)
        
        if MISTRAL_AVAILABLE:
            self.small_client = Mistral(api_key=small_model_key) if small_model_key else None
            self.large_client = Mistral(api_key=large_model_key) if large_model_key else None
        else:
            self.small_client = None
            self.large_client = None
    
    def get_plant_info(self, plant_name: str, stage: str, language: str) -> Dict[str, Any]:
        """Get plant info: try web search + small model, fallback to large model."""
        
        # Try web search first
        if WEB_AVAILABLE and self.small_client:
            try:
                return self._search_and_extract(plant_name, stage, language)
            except Exception as e:
                self.logger.info(f"Web search failed: {e}")
        
        # Fallback to large model
        if self.large_client:
            result = self._ask_large_model(plant_name, stage, language)
            result['source'] = 'mistral_large'
            return result
        
        raise Exception("No AI services available")
    
    def _search_and_extract(self, plant_name: str, stage: str, language: str) -> Dict[str, Any]:
        """Search Boutique Végétale and extract with small model."""
        
        # 1. Find product page
        product_url = self._find_product_url(plant_name)
        if not product_url:
            raise Exception(f"No product found for '{plant_name}'")
        
        # 2. Get page content
        page_content = self._get_page_content(product_url)
        if not page_content:
            raise Exception("Failed to get page content")
        
        # 3. Extract with small model
        plant_data = self._extract_with_small_model(page_content, plant_name, stage, language)
        plant_data['source'] = 'boutique_vegetale'
        plant_data['url'] = product_url
        
        return plant_data
    
    def _find_product_url(self, query: str) -> Optional[str]:
        """Find best matching product URL on Boutique Végétale."""
        
        search_url = f"https://www.boutique-vegetale.com/?s={urllib.parse.quote_plus(query)}&post_type=product"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find best matching product link
        best_match = None
        best_score = 0
        
        for link in soup.select('a[href*="/p/"]'):
            href = link.get('href')
            if href and href.startswith('/'):
                href = "https://www.boutique-vegetale.com" + href
            
            title = link.get_text(strip=True)
            score = self._calculate_match_score(title, query)
            
            if score > best_score:
                best_score = score
                best_match = href
        
        return best_match
    
    def _calculate_match_score(self, title: str, query: str) -> float:
        """Simple relevance scoring."""
        if not title or not query:
            return 0.0
        
        title_lower = title.lower()
        query_lower = query.lower()
        
        # Exact match gets highest score
        if query_lower in title_lower:
            return 1.0
        
        # Word matching
        query_words = query_lower.split()
        title_words = title_lower.split()
        
        matches = sum(1 for word in query_words 
                     if any(word in title_word for title_word in title_words))
        
        return matches / len(query_words) if query_words else 0.0
    
    def _get_page_content(self, url: str) -> str:
        """Get clean page content for AI processing."""
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove unwanted elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer']):
            element.decompose()
        
        # Get main content
        content_parts = []
        
        # Title
        title = soup.find('title')
        if title:
            content_parts.append(f"TITLE: {title.get_text(strip=True)}")
        
        # Main content
        main = soup.find('main') or soup.find('body')
        if main:
            content_parts.append(f"CONTENT: {main.get_text(strip=True)}")
        
        # Clean and limit content
        full_content = "\n\n".join(content_parts)
        full_content = re.sub(r'\s+', ' ', full_content).strip()
        
        # Limit to avoid token limits
        if len(full_content) > 6000:
            full_content = full_content[:6000] + "..."
        
        return full_content
    
    def _extract_with_small_model(self, content: str, plant_name: str, stage: str, language: str) -> Dict[str, Any]:
        """Extract plant data using ministral-8b-latest."""
        
        prompt = self._build_extraction_prompt(content, plant_name, stage, language)
        
        response = self.small_client.chat.complete(
            model="ministral-8b-latest",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"}
        )
        
        ai_response = response.choices[0].message.content.strip()
        plant_data = json.loads(ai_response)
        
        # Basic validation
        if not plant_data.get('common') and not plant_data.get('latin'):
            raise Exception("AI extraction failed - missing plant names")
        
        return plant_data
    
    def _ask_large_model(self, plant_name: str, stage: str, language: str) -> Dict[str, Any]:
        """Fallback to mistral-large-latest for plant info."""
        
        prompt = self._build_fallback_prompt(plant_name, stage, language)
        
        response = self.large_client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
            response_format={"type": "json_object"}
        )
        
        ai_response = response.choices[0].message.content.strip()
        return json.loads(ai_response)
    
    def _build_extraction_prompt(self, content: str, plant_name: str, stage: str, language: str) -> str:
        """Build prompt for extracting from web page."""
        
        lang_examples = {
            "en": "Water regularly and provide full sun. Seeds need warm conditions. Soak seeds 24h before sowing.",
            "fr": "Arroser régulièrement et fournir le plein soleil. Les graines ont besoin de chaleur. Tremper les graines 24h avant semis.",
            "ru": "Регулярно поливать и обеспечить полное солнце. Семена нуждаются в тепле. Замочить семена на 24 часа."
        }
        
        example = lang_examples.get(language, lang_examples["en"])
        
        return f"""Extract plant information from this French plant store page in {language.upper()}.

PLANT SEARCHED: "{plant_name}"
CURRENT STAGE: "{stage}"

WEBPAGE CONTENT:
{content}

Return JSON with these fields:
{{
    "common": "Common name in {language.upper()}",
    "latin": "Scientific name in Latin",
    "notes": "Detailed growing tips in {language.upper()} (4-5 sentences with specific advice like temperature, soil, watering, light requirements)",
    "event_range_min": "Min germination time (number only, e.g. 1)",
    "event_range_min_unit": "Min germination unit (days/weeks/months)",
    "event_range_max": "Max germination time (number only, e.g. 3)", 
    "event_range_max_unit": "Max germination unit (days/weeks/months)",
    "event_dur_val": "Soaking time (number only, e.g. 24)",
    "event_dur_unit": "Soaking unit (hours/days)"
}}

IMPORTANT:
- Extract the EXACT numbers and units from the French text
- For "1 à 3 mois" use: event_range_min: 1, event_range_min_unit: "months", event_range_max: 3, event_range_max_unit: "months"
- For "24 heures" use: event_dur_val: 24, event_dur_unit: "hours"
- Only include timing fields if relevant to the stage and mentioned in content
Extract as much growing advice as possible for the notes field.
Response (JSON only):"""
    
    def _build_fallback_prompt(self, plant_name: str, stage: str, language: str) -> str:
        """Build prompt for large model fallback."""
        
        lang_examples = {
            "en": "Water regularly and provide full sun. Seeds need warm conditions. Soak seeds 24h before sowing.",
            "fr": "Arroser régulièrement et fournir le plein soleil. Les graines ont besoin de chaleur. Tremper les graines 24h avant semis.", 
            "ru": "Регулярно поливать и обеспечить полное солнце. Семена нуждаются в тепле. Замочить семена на 24 часа."
        }
        
        example = lang_examples.get(language, lang_examples["en"])
        
        return f"""You are a botanical expert. Provide detailed information for "{plant_name}" in stage "{stage}".

Respond in {language.upper()} language.

Return JSON:
{{
    "common": "Common name in {language.upper()}",
    "latin": "Scientific name in Latin",
    "notes": "Detailed growing tips in {language.upper()} (4-5 sentences with specific advice like temperature, soil, watering, light requirements)",
    "event_range_min": "Min germination time (number only, e.g. 1)",
    "event_range_min_unit": "Min germination unit (days/weeks/months)",
    "event_range_max": "Max germination time (number only, e.g. 3)",
    "event_range_max_unit": "Max germination unit (days/weeks/months)",
    "event_dur_val": "Soaking time (number only, e.g. 24)",
    "event_dur_unit": "Soaking unit (hours/days)"
}}

IMPORTANT:
- Extract the EXACT numbers and units mentioned in text
- Only include timing fields if relevant to the current stage
Provide comprehensive growing advice in the notes field.
Response (JSON only):"""


def get_plant_info(config: Dict[str, Any], plant_name: str, stage: str, language: str) -> Dict[str, Any]:
    """Main function to get plant information."""
    
    # Get API keys from config
    small_key = config.get("mistral_small", {}).get("api_key")
    large_key = config.get("mistral_large", {}).get("api_key")
    
    if not small_key and not large_key:
        raise Exception("No API keys configured")
    
    service = PlantAIService(small_key, large_key)
    return service.get_plant_info(plant_name, stage, language)