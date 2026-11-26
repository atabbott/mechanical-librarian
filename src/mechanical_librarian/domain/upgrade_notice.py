"""The owner's notice after an upgrade regenerated the derived rows (#2039).

ADR-0108: Activity and Attention show one notice per regeneration, counting
the owner's answers that were kept and those asked again, until the owner
dismisses it. The regeneration and its dismissal are recorded in the ledger
(#2040, `RegenerationNoticeStore`); this module holds only the notice the
browser is shown from that read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

UpgradeNoticeSchemaVersion = Literal["upgrade-notice-v1"]
UPGRADE_NOTICE_SCHEMA: Final[UpgradeNoticeSchemaVersion] = "upgrade-notice-v1"


@dataclass(frozen=True, slots=True)
class UpgradeNotice:
    """What the browser is shown: nothing once dismissed.

    `regeneration_id` is the ledger's regeneration id in decimal, as the
    dismissal's path names it; `preparing` says review preparation is running
    now, so the counts are so far. `not_yet_checked` counts the answers no
    new case has checked yet, so an idle notice that still has some says
    no total.
    """

    regeneration_id: str
    preparing: bool
    kept: int
    asked_again: int
    not_yet_checked: int

    def to_data(self) -> dict[str, object]:
        return {
            "regeneration_id": self.regeneration_id,
            "preparing": self.preparing,
            "kept": self.kept,
            "asked_again": self.asked_again,
            "not_yet_checked": self.not_yet_checked,
        }
