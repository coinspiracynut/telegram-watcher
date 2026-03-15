"""
Token extraction utilities for parsing Rick messages.
"""
import json
import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse


# Solana base58 address pattern: 32-44 chars of [1-9A-HJ-NP-Za-km-z]
_SOLANA_ADDR_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


def extract_token_addresses(raw_json: Optional[str]) -> List[Tuple[str, str]]:
    """
    Extract token info from Rick messages.
    
    - **Address** from Photon URLs (correct base58 casing, actual mint address).
      Format: https://photon-sol.tinyastro.io/en/r/@RickBurpBot/{mint_address}
    - **Network** from DexScreener URLs (e.g. solana, ethereum, base).
      Format: https://dexscreener.com/{network}/{pair_address}
    
    Falls back to "solana" if no DexScreener URL is found (Photon is Solana-only).
    
    Returns list of (network, address) tuples.
    """
    if not raw_json:
        return []
    
    try:
        data = json.loads(raw_json)
        entities = data.get("entities", [])
        
        # 1. Get network from DexScreener URL
        network = None
        for entity in entities:
            if entity.get("_") == "MessageEntityTextUrl":
                url = entity.get("url", "")
                if "dexscreener.com" in url:
                    parsed = urlparse(url)
                    path_parts = [p for p in parsed.path.split("/") if p]
                    if path_parts:
                        network = path_parts[0]  # e.g. "solana", "ethereum"
                        break
        
        # Default to solana (Photon is Solana-only)
        if not network:
            network = "solana"
        
        # 2. Get token mint address from Photon URL (correct casing)
        found = set()
        results = []
        
        for entity in entities:
            if entity.get("_") == "MessageEntityTextUrl":
                url = entity.get("url", "")
                if "photon-sol.tinyastro.io" in url:
                    parsed = urlparse(url)
                    path_parts = [p for p in parsed.path.split("/") if p]
                    if path_parts:
                        candidate = path_parts[-1]
                        if _SOLANA_ADDR_RE.fullmatch(candidate) and candidate not in found:
                            found.add(candidate)
                            results.append((network, candidate))
        
        return results
    except (json.JSONDecodeError, KeyError, IndexError):
        return []


# Keep old name as alias so existing imports don't break
extract_dexscreener_urls = extract_token_addresses


def extract_photon_pool_id(raw_json: Optional[str]) -> Optional[int]:
    """
    Extract Photon pool_id from message URLs.
    
    Looks for URLs like: https://photon-sol.tinyastro.io/en/lp/{pool_id}
    
    Returns pool_id if found, None otherwise.
    """
    if not raw_json:
        return None
    
    try:
        data = json.loads(raw_json)
        entities = data.get("entities", [])
        message_text = data.get("message", "")
        
        # Check entities for Photon URLs
        for entity in entities:
            if entity.get("_") == "MessageEntityTextUrl":
                url = entity.get("url", "")
                if "photon-sol.tinyastro.io" in url:
                    # Parse: https://photon-sol.tinyastro.io/en/lp/{pool_id}
                    parsed = urlparse(url)
                    path_parts = [p for p in parsed.path.split("/") if p]
                    
                    # Look for "lp" followed by pool_id
                    if "lp" in path_parts:
                        lp_index = path_parts.index("lp")
                        if lp_index + 1 < len(path_parts):
                            try:
                                pool_id = int(path_parts[lp_index + 1])
                                return pool_id
                            except (ValueError, IndexError):
                                pass
        
        # Also check message text directly for Photon URLs
        if message_text:
            photon_match = re.search(r"photon-sol\.tinyastro\.io[^\s]*/lp/(\d+)", message_text)
            if photon_match:
                try:
                    return int(photon_match.group(1))
                except ValueError:
                    pass
        
        return None
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def parse_token_name_and_ticker(raw_message_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse token name and ticker from first line of message.
    
    Format: "Token Name [478K/99%] $TICKER"
    - Name: text before the first '['
    - Ticker: text after ']' that starts with '$'
    
    Returns: (token_name, token_ticker)
    """
    if not raw_message_text:
        return None, None
    
    # Get first line
    first_line = raw_message_text.split("\n")[0].strip()
    
    # Find token name (everything before the first '[')
    name_match = re.match(r"^([^[]+)", first_line)
    token_name = name_match.group(1).strip() if name_match else None
    
    # Find ticker (after ']' and starts with '$')
    ticker_match = re.search(r"\]\s*\$([A-Za-z0-9]+)", first_line)
    token_ticker = ticker_match.group(1) if ticker_match else None
    
    # Clean up name (remove emoji prefix if present)
    if token_name:
        # Remove leading emoji/whitespace
        token_name = re.sub(r"^[💊🐦]\s*", "", token_name).strip()
    
    return token_name, token_ticker
