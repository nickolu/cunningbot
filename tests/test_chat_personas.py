"""Unit tests for chat personas."""

import pytest
from bot.domain.chat.chat_personas import CHAT_PERSONAS


class TestChatPersonas:
    """Tests for CHAT_PERSONAS configuration."""

    def test_all_personas_exist(self) -> None:
        """Test that all expected personas are defined."""
        expected_personas = [
            "discord_user",
            "cat",
            "helpful_assistant",
            "sarcastic_jerk",
            "homer_simpson"
        ]

        for persona_id in expected_personas:
            assert persona_id in CHAT_PERSONAS, f"Persona '{persona_id}' is missing"

    def test_all_personas_have_name(self) -> None:
        """Test that all personas have a name field."""
        for persona_id, persona_data in CHAT_PERSONAS.items():
            assert "name" in persona_data, f"Persona '{persona_id}' missing 'name' field"
            assert isinstance(persona_data["name"], str), f"Persona '{persona_id}' name is not a string"
            assert len(persona_data["name"]) > 0, f"Persona '{persona_id}' name is empty"

    def test_all_personas_have_instructions_or_personality(self) -> None:
        """Test that all personas have either instructions or personality."""
        for persona_id, persona_data in CHAT_PERSONAS.items():
            has_instructions = "instructions" in persona_data
            has_personality = "personality" in persona_data

            assert has_instructions or has_personality, \
                f"Persona '{persona_id}' must have either 'instructions' or 'personality'"

    def test_discord_user_persona(self) -> None:
        """Test discord_user persona configuration."""
        persona = CHAT_PERSONAS["discord_user"]

        assert persona["name"] == "A discord user"
        assert "instructions" in persona
        assert "discord" in persona["instructions"].lower()
        assert "chat" in persona["instructions"].lower()

    def test_cat_persona(self) -> None:
        """Test cat persona configuration."""
        persona = CHAT_PERSONAS["cat"]

        assert persona["name"] == "Cat"
        # Cat uses 'personality' instead of 'instructions'
        assert "personality" in persona
        assert "cat" in persona["personality"].lower()
        assert "meow" in persona["personality"].lower()

    def test_helpful_assistant_persona(self) -> None:
        """Test helpful_assistant persona configuration."""
        persona = CHAT_PERSONAS["helpful_assistant"]

        assert persona["name"] == "Helpful Assistant"
        assert "instructions" in persona
        assert "helpful" in persona["instructions"].lower()

    def test_sarcastic_jerk_persona(self) -> None:
        """Test sarcastic_jerk persona configuration."""
        persona = CHAT_PERSONAS["sarcastic_jerk"]

        assert persona["name"] == "Sarcastic Jerk"
        assert "instructions" in persona
        assert "sarcastic" in persona["instructions"].lower()

    def test_homer_simpson_persona(self) -> None:
        """Test homer_simpson persona configuration."""
        persona = CHAT_PERSONAS["homer_simpson"]

        assert persona["name"] == "Homer Simpson"
        assert "instructions" in persona
        assert "homer simpson" in persona["instructions"].lower()
        assert "character" in persona["instructions"].lower()

    def test_no_empty_strings(self) -> None:
        """Test that no persona has empty instruction/personality strings."""
        for persona_id, persona_data in CHAT_PERSONAS.items():
            if "instructions" in persona_data:
                assert len(persona_data["instructions"]) > 0, \
                    f"Persona '{persona_id}' has empty instructions"

            if "personality" in persona_data:
                assert len(persona_data["personality"]) > 0, \
                    f"Persona '{persona_id}' has empty personality"

    def test_personas_dict_is_not_empty(self) -> None:
        """Test that CHAT_PERSONAS dictionary is not empty."""
        assert len(CHAT_PERSONAS) > 0, "CHAT_PERSONAS dictionary is empty"

    def test_personas_count(self) -> None:
        """Test that we have the expected number of personas."""
        assert len(CHAT_PERSONAS) == 5, f"Expected 5 personas, found {len(CHAT_PERSONAS)}"

    def test_persona_names_are_unique(self) -> None:
        """Test that all persona names are unique."""
        names = [persona["name"] for persona in CHAT_PERSONAS.values()]
        assert len(names) == len(set(names)), "Persona names are not unique"

    def test_persona_ids_are_lowercase(self) -> None:
        """Test that all persona IDs use lowercase with underscores."""
        for persona_id in CHAT_PERSONAS.keys():
            assert persona_id.islower() or "_" in persona_id, \
                f"Persona ID '{persona_id}' should be lowercase with underscores"
            assert " " not in persona_id, \
                f"Persona ID '{persona_id}' should not contain spaces"

    def test_instructions_are_descriptive(self) -> None:
        """Test that instructions provide meaningful guidance."""
        min_instruction_length = 10

        for persona_id, persona_data in CHAT_PERSONAS.items():
            if "instructions" in persona_data:
                assert len(persona_data["instructions"]) >= min_instruction_length, \
                    f"Persona '{persona_id}' instructions too short"

            if "personality" in persona_data:
                assert len(persona_data["personality"]) >= min_instruction_length, \
                    f"Persona '{persona_id}' personality description too short"

    def test_persona_data_types(self) -> None:
        """Test that persona data has correct types."""
        for persona_id, persona_data in CHAT_PERSONAS.items():
            assert isinstance(persona_data, dict), \
                f"Persona '{persona_id}' data should be a dictionary"

            assert isinstance(persona_data["name"], str), \
                f"Persona '{persona_id}' name should be a string"

            if "instructions" in persona_data:
                assert isinstance(persona_data["instructions"], str), \
                    f"Persona '{persona_id}' instructions should be a string"

            if "personality" in persona_data:
                assert isinstance(persona_data["personality"], str), \
                    f"Persona '{persona_id}' personality should be a string"

    def test_can_access_persona_by_id(self) -> None:
        """Test that personas can be accessed by their ID."""
        persona = CHAT_PERSONAS.get("discord_user")
        assert persona is not None
        assert persona["name"] == "A discord user"

    def test_accessing_nonexistent_persona_returns_none(self) -> None:
        """Test that accessing non-existent persona returns None."""
        persona = CHAT_PERSONAS.get("nonexistent_persona")
        assert persona is None
