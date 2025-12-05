"""
Monetization utility - Adsterra ad page integration
"""
import urllib.parse

# ======================
# CONFIGURATION
# ======================

# Your GitHub Pages URL with Adsterra ads
GITHUB_PAGE_URL = "https://adspage.onrender.com"

# Your bot username (without @)
BOT_USERNAME = "@File_AssistBot"  # ⚠️ CHANGE THIS!

# Enable/Disable monetization
# True = Users go through ad page
# False = Direct file delivery (no ads)
MONETIZATION_ENABLED = True


def create_ad_link(
    token: str,
    movie_name: str = "",
    part: int = 1,
    quality: str = "",
    file_size: str = "",
    bot_username: str = None
) -> str:
    """
    Create ad page link that redirects back to bot with token
    
    Flow: Bot → Ad Page (shows ads) → Back to Bot → File sent
    """
    bot = bot_username or BOT_USERNAME
    
    # Callback URL (where ad page redirects after ads)
    callback_url = f"https://t.me/{bot}?start=token_{token}"
    
    # Parameters for ad page display
    params = {
        "token": token,
        "movie": movie_name,
        "part": str(part),
        "quality": quality,
        "size": file_size,
        "callback": callback_url
    }
    
    # Remove empty params
    params = {k: v for k, v in params.items() if v}
    
    query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"{GITHUB_PAGE_URL}?{query_string}"


def is_monetization_enabled() -> bool:
    """Check if monetization is enabled"""
    return MONETIZATION_ENABLED and GITHUB_PAGE_URL and "YOUR_GITHUB_USERNAME" not in GITHUB_PAGE_URL