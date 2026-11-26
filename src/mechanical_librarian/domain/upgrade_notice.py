"""The owner's notice after an upgrade regenerated the derived rows (#2039).

ADR-0108: Activity and Attention show one notice per regeneration, counting
the owner's answers that were kept and those asked again, until the owner
dismisses it. The regeneration and its dismissal are recorded by the
regeneration itself (#2040); this module holds only the summary that read
answers and the notice the browser is shown from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

UpgradeNoticeSchemaVersion = Literal["upgrade-notice-v1"]
UPGRADE_NOTICE_SCHEMA: Final[UpgradeNoticeSchemaVersion] = "upgrade-notice-v1"


class UpgradeNoticeError(RuntimeError):
    """A dismissal of a regeneration that is not the latest one."""

    def __init__(self, code: Literal["stale"]) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DerivedRegeneration:
    """The latest regeneration of the derived rows, as its record states it.

    `kept` counts the answers carried to the new cases; `asked_again` only
    those on fields the new case asks about; `not_yet_checked` the first new
    cases still to be checked.
    """

    regeneration_id: str
    epoch: int
    finished_at: int
    kept: int
    asked_again: int
    not_yet_checked: int
    dismissed: bool


@dataclass(frozen=True, slots=True)
class UpgradeNotice:
    """What the browser is shown: nothing once dismissed."""

    regeneration_id: str
    preparing: bool
    kept: int
    asked_again: int

    @classmethod
    def of(cls, regeneration: DerivedRegeneration | None) -> UpgradeNotice | None:
        if regeneration is None or regeneration.dismissed:
            return None
        return cls(
            regeneration.regeneration_id,
            regeneration.not_yet_checked > 0,
            regeneration.kept,
            regeneration.asked_again,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "regeneration_id": self.regeneration_id,
            "preparing": self.preparing,
            "kept": self.kept,
            "asked_again": self.asked_again,
        }
