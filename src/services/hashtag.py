"""Hashtag normalization and suggestion service."""

import re
from typing import List, Optional

import httpx

from src.config import get_settings

settings = get_settings()

# Hashtag validation constants
MAX_HASHTAG_LENGTH = 50
MAX_HASHTAG_COUNT = 15
HASHTAG_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")

# Characters permitted to appear in a raw tag before normalization.
# Alphanumerics and underscores are kept; space, tab, hyphen and slash act as
# word separators and are merged out ("test-tag" -> "testtag").
# Anything else - "!", "@", emoji, ... - makes the whole tag invalid, rather
# than being silently stripped and yielding a different, unintended tag.
HASHTAG_ALLOWED_CHARS = re.compile(r"^[a-zA-Z0-9_ \t/-]*$")


class HashtagService:
    """Service for normalizing and suggesting hashtags."""

    def normalize_hashtag(self, tag: str) -> Optional[str]:
        """Normalize a single hashtag.

        - Removes # prefix if present
        - Converts to lowercase
        - Merges separator characters (space, tab, hyphen, slash)
        - Returns None if the tag contains any other invalid character
        - Returns None if the result is empty or too long
        """
        # Remove # prefix
        tag = tag.lstrip("#").strip()

        # Reject outright rather than stripping: "#invalid!" is a bad tag, it is
        # not the tag "invalid".
        if not HASHTAG_ALLOWED_CHARS.match(tag):
            return None

        # Merge separators away (keep alphanumeric and underscore)
        tag = re.sub(r"[^a-zA-Z0-9_]", "", tag)

        # Convert to lowercase
        tag = tag.lower()

        # Validate length and format
        if not tag or len(tag) > MAX_HASHTAG_LENGTH:
            return None

        if not HASHTAG_PATTERN.match(tag):
            return None

        return tag

    def normalize_hashtags(self, tags: List[str]) -> List[str]:
        """Normalize a list of hashtags, removing duplicates and invalid ones."""
        normalized = []
        seen = set()

        for tag in tags:
            normalized_tag = self.normalize_hashtag(tag)
            if normalized_tag and normalized_tag not in seen:
                normalized.append(normalized_tag)
                seen.add(normalized_tag)

        # Enforce maximum count
        return normalized[:MAX_HASHTAG_COUNT]

    def format_hashtags_for_youtube(self, tags: List[str]) -> List[str]:
        """Format hashtags for YouTube (with # prefix)."""
        return [f"#{tag}" for tag in self.normalize_hashtags(tags)]

    def combine_tags_and_hashtags(
        self, tags: List[str], hashtags: List[str]
    ) -> List[str]:
        """Combine regular tags and hashtags into a single list."""
        all_tags = []
        seen = set()

        # Add regular tags first
        for tag in tags:
            tag = tag.strip().lower()
            if tag and tag not in seen:
                all_tags.append(tag)
                seen.add(tag)

        # Add hashtags (without # prefix for YouTube tags)
        for tag in hashtags:
            normalized = self.normalize_hashtag(tag)
            if normalized and normalized not in seen:
                all_tags.append(normalized)
                seen.add(normalized)

        return all_tags[:500]  # YouTube tag limit

    async def suggest_hashtags(
        self,
        title: str,
        description: str = "",
        category: str = "",
        count: int = 10,
    ) -> List[str]:
        """Suggest hashtags based on content and trends.
        
        NOTE: This is a placeholder implementation. In production, integrate with:
        - YouTube Data API trending videos
        - Third-party hashtag suggestion APIs (with proper licensing)
        - Your own analytics of successful uploads
        
        Limitations:
        - Suggestions are based on common patterns, not real-time trends
        - Regional and language limitations may apply
        - API quotas and rate limits apply to external services
        """
        if not settings.hashtag_suggestion_api_key:
            # Return basic suggestions based on title keywords
            return self._generate_basic_suggestions(title, description, count)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    settings.hashtag_suggestion_api_url,
                    params={
                        "query": f"{title} {description}",
                        "count": count,
                    },
                    headers={"Authorization": f"Bearer {settings.hashtag_suggestion_api_key}"},
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()
                return self.normalize_hashtags(data.get("hashtags", []))
        except Exception:
            # Fallback to basic suggestions on API failure
            return self._generate_basic_suggestions(title, description, count)

    def _generate_basic_suggestions(
        self, title: str, description: str, count: int
    ) -> List[str]:
        """Generate basic hashtag suggestions from content analysis."""
        # Common video category hashtags
        category_hashtags = {
            "gaming": ["gaming", "gamer", "gamingcommunity", "videogames"],
            "tech": ["tech", "technology", "innovation", "technews"],
            "music": ["music", "newmusic", "musician", "song"],
            "tutorial": ["tutorial", "howto", "learnsomethingnew", "education"],
            "vlog": ["vlog", "dailyvlog", "lifestyle", "dayinthelife"],
            "cooking": ["cooking", "recipe", "foodie", "homemade"],
            "fitness": ["fitness", "workout", "healthylifestyle", "gym"],
            "travel": ["travel", "wanderlust", "adventure", "explore"],
        }

        # Extract keywords from title
        title_words = set(title.lower().split())
        suggestions = []

        # Find matching categories
        for category, tags in category_hashtags.items():
            if category in title_words:
                suggestions.extend(tags[:2])

        # Add generic popular hashtags
        generic_tags = [
            "video",
            "youtube",
            "contentcreator",
            "trending",
            "viral",
            "newvideo",
        ]
        suggestions.extend(generic_tags[:count - len(suggestions)])

        return self.normalize_hashtags(suggestions)[:count]


# Singleton instance
hashtag_service = HashtagService()
