"""
Unit tests for AuthorityClassifier.

Tests the rule-based authority inference logic.
"""
import pytest
from app.domain.types.authority import AuthorityLevel
from app.domain.services.authority_classifier import AuthorityClassifier


class TestAuthorityClassifier:
    """Test suite for AuthorityClassifier service."""
    
    def test_rubric_path_returns_constitution(self):
        """Rubric paths should return CONSTITUTION (highest authority)."""
        assert AuthorityClassifier.classify("/rubrics/math_rubric.pdf") == AuthorityLevel.CONSTITUTION
        assert AuthorityClassifier.classify("/evaluacion/final_exam.pdf") == AuthorityLevel.CONSTITUTION
        assert AuthorityClassifier.classify("/grading/policy.docx") == AuthorityLevel.CONSTITUTION
    
    def test_policy_path_returns_policy(self):
        """Operational policy and admin paths should return POLICY."""
        assert AuthorityClassifier.classify("/policy/incident-response.pdf") == AuthorityLevel.POLICY
        assert AuthorityClassifier.classify("/admin/horario.xlsx") == AuthorityLevel.POLICY
        assert AuthorityClassifier.classify("/calendario/2024.pdf") == AuthorityLevel.POLICY
    
    def test_canonical_path_returns_canonical(self):
        """Canonical manual paths should return CANONICAL."""
        assert AuthorityClassifier.classify("/standards/incident_manual.pdf") == AuthorityLevel.CANONICAL
        assert AuthorityClassifier.classify("/reference/operations_spec.pdf") == AuthorityLevel.CANONICAL
        assert AuthorityClassifier.classify("/manual/oficial.pdf") == AuthorityLevel.CANONICAL
    
    def test_generic_path_returns_supplementary(self):
        """Unknown/generic paths should return SUPPLEMENTARY (default)."""
        assert AuthorityClassifier.classify("/uploads/random_article.pdf") == AuthorityLevel.SUPPLEMENTARY
        assert AuthorityClassifier.classify("/docs/notes.txt") == AuthorityLevel.SUPPLEMENTARY
        assert AuthorityClassifier.classify("") == AuthorityLevel.SUPPLEMENTARY
        assert AuthorityClassifier.classify(None) == AuthorityLevel.SUPPLEMENTARY
    
    def test_doc_type_overrides_path(self):
        """Doc type should be considered in classification."""
        # Path is generic, but doc_type indicates rubric
        result = AuthorityClassifier.classify(
            storage_path="/uploads/document.pdf",
            doc_type="rubric"
        )
        assert result == AuthorityLevel.CONSTITUTION
    
    def test_filename_considered(self):
        """Filename patterns should be considered."""
        result = AuthorityClassifier.classify(
            storage_path="/uploads/",
            filename="rubrica_evaluacion.pdf"
        )
        assert result == AuthorityLevel.CONSTITUTION
    
    def test_case_insensitivity(self):
        """Classification should be case-insensitive."""
        assert AuthorityClassifier.classify("/RUBRICS/Test.pdf") == AuthorityLevel.CONSTITUTION
        assert AuthorityClassifier.classify("/POLICY/OPERATIONS.pdf") == AuthorityLevel.POLICY
    
    def test_priority_order(self):
        """CONSTITUTION rules should take priority over lower levels."""
        # Path contains both constitution and canonical keywords
        result = AuthorityClassifier.classify("/standards/rubric_guide.pdf")
        assert result == AuthorityLevel.CONSTITUTION  # First match wins


class TestAuthorityLevelEnum:
    """Test suite for AuthorityLevel enum."""
    
    def test_weights_are_ordered(self):
        """Higher authority should have higher weight."""
        const_weight = AuthorityLevel.get_weight(AuthorityLevel.CONSTITUTION)
        policy_weight = AuthorityLevel.get_weight(AuthorityLevel.POLICY)
        canon_weight = AuthorityLevel.get_weight(AuthorityLevel.CANONICAL)
        supp_weight = AuthorityLevel.get_weight(AuthorityLevel.SUPPLEMENTARY)
        
        assert const_weight > policy_weight > canon_weight > supp_weight
    
    def test_string_serialization(self):
        """Enum values should serialize to expected strings."""
        assert AuthorityLevel.CONSTITUTION.value == "constitution"
        assert AuthorityLevel.POLICY.value == "policy"
        assert AuthorityLevel.CANONICAL.value == "canonical"
        assert AuthorityLevel.SUPPLEMENTARY.value == "supplementary"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
