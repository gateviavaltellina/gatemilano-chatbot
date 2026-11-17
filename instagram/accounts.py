"""Account Instagram ammessi: una sola mappa per ricezione, invio e anti-loop."""
import logging
from config import settings

logger = logging.getLogger(__name__)

_MILANO_IDS = {"35517015101275600", "17841405933946552"}
_SARDINIA_IDS = {"24588954374135134", "17841452139166980"}


def account_venues() -> dict[str, str]:
    accounts = {account: "gate_milano" for account in _MILANO_IDS}
    accounts.update({account: "gate_sardinia" for account in _SARDINIA_IDS})
    for account, venue in (
        (settings.ig_gatemilano_id, "gate_milano"),
        (settings.ig_gatesardinia_id, "gate_sardinia"),
        (settings.ig_schoolfestival_id, "milano_school_festival"),
    ):
        account = account.strip()
        if account:
            # Una configurazione errata non deve reindirizzare un account Gate.
            if account in accounts and accounts[account] != venue:
                logger.error("ID Instagram in conflitto per %s: configurazione ignorata", venue)
                continue
            accounts[account] = venue
    return accounts


def venue_for_account(account: str) -> str | None:
    return account_venues().get(account)
