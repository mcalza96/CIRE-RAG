"""
Config Tuner - Automated Parameter Adjustment Recommendations.

Analyzes validation results and suggests/applies parameter adjustments
to improve institutional rule compliance when failure rate exceeds threshold.
"""
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS & ENUMS
# =============================================================================

FAILURE_THRESHOLD_CRITICAL = 5.0
FAILURE_THRESHOLD_WARNING = 2.0

class SeverityLevel(str, Enum):
    NONE = "none"
    WARNING = "warning"
    CRITICAL = "critical"

# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ParameterAdjustment:
    parameter: str
    currentValue: float
    suggestedValue: float
    delta: float
    reason: str
    severity: str

@dataclass
class TuningRecommendation:
    timestamp: str
    failureRate: float
    severity: str
    adjustments: List[ParameterAdjustment]
    autoApplied: bool
    notes: str
    
    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "failureRate": self.failureRate,
            "severity": self.severity,
            "adjustments": [asdict(a) for a in self.adjustments],
            "autoApplied": self.autoApplied,
            "notes": self.notes
        }
    
    def to_markdown(self) -> str:
        if self.severity == SeverityLevel.NONE.value:
            return "## Tuning Recommendation\n\n✅ No adjustments needed. System is performing well."
        
        md = f"""## Tuning Recommendation

**Severity**: {'🟡 Warning' if self.severity == 'warning' else '🔴 Critical'}
**Failure Rate**: {self.failureRate:.2f}%
**Timestamp**: {self.timestamp}

### Suggested Adjustments

| Parameter | Current | Suggested | Delta | Reason |
|-----------|---------|-----------|-------|--------|
"""
        for adj in self.adjustments:
            md += f"| {adj.parameter} | {adj.currentValue:.2f} | {adj.suggestedValue:.2f} | {adj.delta:+.2f} | {adj.reason} |\n"
        
        md += f"\n**Notes**: {self.notes}\n"
        
        if self.autoApplied:
            md += "\n⚡ **These adjustments have been automatically applied.**\n"
        else:
            md += "\n📋 Review and apply these adjustments manually.\n"
        
        return md

# =============================================================================
# INTERFACES
# =============================================================================

class IConfigManager(ABC):
    @abstractmethod
    def load(self) -> Dict: pass
    @abstractmethod
    def save(self, config: Optional[Dict] = None) -> None: pass
    @abstractmethod
    def get(self, key: str, default=None): pass
    @abstractmethod
    def set(self, key: str, value) -> None: pass
    @abstractmethod
    def backup(self) -> str: pass

class ITuningStrategy(ABC):
    @abstractmethod
    def calculate_adjustment(self, current_config: Dict, severity: SeverityLevel) -> Optional[ParameterAdjustment]: pass

# =============================================================================
# INFRASTRUCTURE
# =============================================================================

class ConfigManager(IConfigManager):
    DEFAULT_CONFIG = {
        "gravity_weight": 0.5,
        "logit_bias_strength": 10,
        "temperature": 0.3,
        "top_p": 0.9,
        "authority_boost": 1.5,
        "version": 1
    }
    
    def __init__(self, config_path: str = "config/rag_tuning.json"):
        self.config_path = config_path
        self._config: Optional[Dict] = None
    
    def load(self) -> Dict:
        try:
            with open(self.config_path, 'r') as f:
                self._config = json.load(f)
        except FileNotFoundError:
            logger.info(f"No config at {self.config_path}, using defaults")
            self._config = self.DEFAULT_CONFIG.copy()
        return self._config
    
    def save(self, config: Optional[Dict] = None) -> None:
        config = config or self._config
        if config is None:
            raise ValueError("No config to save")
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        config["version"] = config.get("version", 0) + 1
        config["last_updated"] = datetime.utcnow().isoformat()
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=2)
        self._config = config
    
    def get(self, key: str, default=None):
        if self._config is None: self.load()
        return self._config.get(key, default)
    
    def set(self, key: str, value) -> None:
        if self._config is None: self.load()
        self._config[key] = value
    
    def backup(self) -> str:
        if self._config is None: self.load()
        backup_path = f"{self.config_path}.backup.{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        with open(backup_path, 'w') as f:
            json.dump(self._config, f, indent=2)
        return backup_path

# =============================================================================
# STRATEGIES
# =============================================================================

class GravityWeightStrategy(ITuningStrategy):
    INCREMENTS = {"warning": 0.1, "critical": 0.2, "max": 1.0}
    
    def calculate_adjustment(self, config: Dict, severity: SeverityLevel) -> Optional[ParameterAdjustment]:
        if severity == SeverityLevel.NONE: return None
        current = config.get("gravity_weight", 0.5)
        delta = self.INCREMENTS[severity.value]
        new_val = min(current + delta, self.INCREMENTS["max"])
        
        if new_val > current:
            return ParameterAdjustment(
                parameter="gravity_weight", currentValue=current, suggestedValue=new_val,
                delta=delta, reason="Favor authoritative nodes", severity=severity.value
            )
        return None

class LogitBiasStrategy(ITuningStrategy):
    INCREMENTS = {"warning": 2, "critical": 5, "max": 20}
    
    def calculate_adjustment(self, config: Dict, severity: SeverityLevel) -> Optional[ParameterAdjustment]:
        if severity == SeverityLevel.NONE: return None
        current = config.get("logit_bias_strength", 10)
        delta = self.INCREMENTS[severity.value]
        new_val = min(current + delta, self.INCREMENTS["max"])
        
        if new_val > current:
            return ParameterAdjustment(
                parameter="logit_bias_strength", currentValue=current, suggestedValue=new_val,
                delta=delta, reason="Force normative vocabulary", severity=severity.value
            )
        return None

class TemperatureStrategy(ITuningStrategy):
    INCREMENTS = {"warning": -0.05, "critical": -0.1, "min": 0.0}
    
    def calculate_adjustment(self, config: Dict, severity: SeverityLevel) -> Optional[ParameterAdjustment]:
        if severity == SeverityLevel.NONE: return None
        current = config.get("temperature", 0.3)
        delta = self.INCREMENTS[severity.value]
        new_val = max(current + delta, self.INCREMENTS["min"])
        
        if new_val < current:
            return ParameterAdjustment(
                parameter="temperature", currentValue=current, suggestedValue=new_val,
                delta=delta, reason="Reduce randomness", severity=severity.value
            )
        return None

# =============================================================================
# ANALYZER
# =============================================================================

class FailureAnalyzer:
    def analyze(self, failed_cases: List[Dict]) -> str:
        if not failed_cases: return "No patterns detected."
        patterns = []
        
        no_citation = sum(1 for c in failed_cases if "no cit" in c.get("reason", "").lower())
        if no_citation > len(failed_cases) * 0.5:
            patterns.append("Missing citations (logit_bias needed)")
            
        flexibility = sum(1 for c in failed_cases if any(
            w in c.get("ragResponse", "").lower() for w in ["podría", "quizás"]
        ))
        if flexibility > len(failed_cases) * 0.3:
            patterns.append("Excessive flexibility (temp decrease needed)")
            
        return "; ".join(patterns) if patterns else "No specific patterns detected."

# =============================================================================
# CONFIG TUNER (ORCHESTRATOR)
# =============================================================================

class ConfigTuner:
    def __init__(
        self,
        config_manager: IConfigManager,
        analyzer: FailureAnalyzer,
        strategies: List[ITuningStrategy],
        auto_apply: bool = False
    ):
        self.config = config_manager
        self.analyzer = analyzer
        self.strategies = strategies
        self.auto_apply = auto_apply
    
    def analyze(self, failure_rate: float, failed_cases: Optional[List[Dict]] = None) -> TuningRecommendation:
        current_config = self.config.load()
        severity = self._get_severity(failure_rate)
        
        adjustments = []
        if severity != SeverityLevel.NONE:
            for strategy in self.strategies:
                adj = strategy.calculate_adjustment(current_config, severity)
                if adj: adjustments.append(adj)
        
        notes = self.analyzer.analyze(failed_cases or [])
        
        recommendation = TuningRecommendation(
            timestamp=datetime.utcnow().isoformat(),
            failureRate=failure_rate,
            severity=severity.value,
            adjustments=adjustments,
            autoApplied=False,
            notes=notes
        )
        
        if self.auto_apply and adjustments:
            self._apply(adjustments)
            recommendation.autoApplied = True
            
        return recommendation

    def _get_severity(self, rate: float) -> SeverityLevel:
        if rate > FAILURE_THRESHOLD_CRITICAL: return SeverityLevel.CRITICAL
        if rate > FAILURE_THRESHOLD_WARNING: return SeverityLevel.WARNING
        return SeverityLevel.NONE

    def _apply(self, adjustments: List[ParameterAdjustment]) -> None:
        self.config.backup()
        for adj in adjustments:
            self.config.set(adj.parameter, adj.suggestedValue)
        self.config.save()

# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="tests/stress/reports/validation_report.json")
    parser.add_argument("--config", default="config/rag_tuning.json")
    parser.add_argument("--auto-apply", action="store_true")
    args = parser.parse_args()
    
    # DI Composition Root
    mgr = ConfigManager(args.config)
    tuner = ConfigTuner(
        config_manager=mgr,
        analyzer=FailureAnalyzer(),
        strategies=[GravityWeightStrategy(), LogitBiasStrategy(), TemperatureStrategy()],
        auto_apply=args.auto_apply
    )
    
    try:
        with open(args.report, 'r') as f:
            report = json.load(f)
        rec = tuner.analyze(100 - report.get("passRate", 100), report.get("failedDetails", []))
        print(rec.to_markdown())
    except Exception as e:
        print(f"Error: {e}")
