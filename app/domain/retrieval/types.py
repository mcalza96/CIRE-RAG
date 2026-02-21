from enum import Enum

class AuthorityLevel(str, Enum):
    # Level 6: Hard Constraints - Immutable rules
    HARD_CONSTRAINT = "hard_constraint"

    # Level 5: Administrative - System level overrides
    ADMINISTRATIVE = "administrative"

    # Level 4: Institutional law - official norms, controls, and integrity policies
    CONSTITUTION = "constitution"
    
    # Level 3: Operational guidance - procedures, calendars, operating structures
    POLICY = "policy"
    
    # Level 2: Canonical references - approved manuals and official materials
    CANONICAL = "canonical"
    
    # Level 1: Supplementary - Wikipedia, web articles, general notes
    SUPPLEMENTARY = "supplementary"

    @staticmethod
    def get_weight(level: 'AuthorityLevel') -> int:
        weights = {
            AuthorityLevel.HARD_CONSTRAINT: 6,
            AuthorityLevel.ADMINISTRATIVE: 5,
            AuthorityLevel.CONSTITUTION: 4,
            AuthorityLevel.POLICY: 3,
            AuthorityLevel.CANONICAL: 2,
            AuthorityLevel.SUPPLEMENTARY: 1,
        }
        return weights.get(level, 0)
