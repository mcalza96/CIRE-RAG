from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from app.domain.schemas.adversarial_schema import AdversarialTestCase

class IAdversarialGenerator(ABC):
    """Interface for adversarial test case generation."""
    
    @abstractmethod
    async def generate_batch(
        self,
        count: int = 10,
        context: Optional[str] = None
    ) -> List[AdversarialTestCase]:
        """Generate a batch of adversarial test cases."""
        pass

    @abstractmethod
    async def generate_from_rules(
        self,
        rules: List[Dict],
        cases_per_rule: int = 2
    ) -> List[AdversarialTestCase]:
        """Generate test cases based on actual institutional rules."""
        pass
