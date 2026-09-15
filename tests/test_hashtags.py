"""Unit tests for hashtag normalization and suggestions."""

import pytest

from src.services.hashtag import HashtagService


class TestHashtagNormalization:
    """Test hashtag normalization."""

    def setup_method(self):
        self.service = HashtagService()

    def test_normalize_basic_hashtag(self):
        """Test basic hashtag normalization."""
        assert self.service.normalize_hashtag("test") == "test"
        assert self.service.normalize_hashtag("#test") == "test"
        assert self.service.normalize_hashtag("TEST") == "test"

    def test_normalize_hashtag_with_invalid_chars(self):
        """Test hashtag with invalid characters."""
        assert self.service.normalize_hashtag("test-tag") == "testtag"
        assert self.service.normalize_hashtag("test tag") == "testtag"
        assert self.service.normalize_hashtag("test/tag") == "testtag"

    def test_normalize_hashtag_too_long(self):
        """Test hashtag that's too long."""
        long_tag = "a" * 100
        assert self.service.normalize_hashtag(long_tag) is None

    def test_normalize_hashtag_empty(self):
        """Test empty hashtag."""
        assert self.service.normalize_hashtag("") is None
        assert self.service.normalize_hashtag("#") is None

    def test_normalize_hashtags_list(self):
        """Test normalizing a list of hashtags."""
        tags = ["#Test", "test-tag", "valid", "#invalid!", ""]
        result = self.service.normalize_hashtags(tags)
        assert result == ["test", "testtag", "valid"]

    def test_normalize_hashtags_deduplication(self):
        """Test hashtag deduplication."""
        tags = ["test", "#test", "TEST", "Test"]
        result = self.service.normalize_hashtags(tags)
        assert result == ["test"]

    def test_normalize_hashtags_max_count(self):
        """Test maximum hashtag count enforcement."""
        tags = [f"tag{i}" for i in range(20)]
        result = self.service.normalize_hashtags(tags)
        assert len(result) == 15


class TestHashtagFormatting:
    """Test hashtag formatting."""

    def setup_method(self):
        self.service = HashtagService()

    def test_format_for_youtube(self):
        """Test formatting hashtags for YouTube."""
        tags = ["test", "video"]
        result = self.service.format_hashtags_for_youtube(tags)
        assert result == ["#test", "#video"]

    def test_combine_tags_and_hashtags(self):
        """Test combining tags and hashtags."""
        tags = ["tag1", "tag2"]
        hashtags = ["hash1", "hash2"]
        result = self.service.combine_tags_and_hashtags(tags, hashtags)
        assert "tag1" in result
        assert "tag2" in result
        assert "hash1" in result
        assert "hash2" in result

    def test_combine_deduplication(self):
        """Test deduplication when combining."""
        tags = ["test", "video"]
        hashtags = ["test", "youtube"]
        result = self.service.combine_tags_and_hashtags(tags, hashtags)
        assert result.count("test") == 1


class TestHashtagSuggestions:
    """Test hashtag suggestions."""

    def setup_method(self):
        self.service = HashtagService()

    @pytest.mark.asyncio
    async def test_suggest_basic_hashtags(self):
        """Test basic hashtag suggestions without API."""
        suggestions = await self.service.suggest_hashtags(
            title="Gaming tutorial video",
            count=5,
        )
        assert len(suggestions) <= 5
        assert all(isinstance(s, str) for s in suggestions)

    @pytest.mark.asyncio
    async def test_suggest_hashtags_with_category(self):
        """Test hashtag suggestions based on category."""
        suggestions = await self.service.suggest_hashtags(
            title="Cooking recipe pasta",
            count=10,
        )
        # Should include cooking-related hashtags
        assert any("cooking" in s or "recipe" in s for s in suggestions)
