from __future__ import annotations

from ..base import AccountFeature, Recommendation, Signal
from ..context import AccountContext
from ..registry import register
from ..text import format_eur


@register
class BonusDependency(AccountFeature):
    code = "bonus_dependency"
    title = "Держится на бонусах"
    description = (
        "Разблокированных бонусов больше, чем своих денег: когда бонусы кончатся, платить будет нечем. "
        "Сила 0 при равенстве и 1.0, когда своих денег нет совсем."
    )
    group = "finance"
    default_weight = 15.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        account = ctx.account
        bonus, balance = account.bonus_balance, account.balance
        if account.bonus_state != "unlocked" or bonus <= balance:
            return None
        value = min(max(bonus / (bonus + balance) * 2 - 1, 0.0), 1.0)
        return Signal(
            value=value,
            explanation=(
                f"Бонусов {format_eur(bonus)}, а своих денег {format_eur(balance)}: "
                "клиент держится на бонусах"
            ),
            facts={"bonus": format_eur(bonus), "balance": format_eur(balance)},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title="Предложить пополнение с бонусом",
            detail=(
                f"Бонусы ({signal.facts['bonus']}) скоро закончатся, а своих денег {signal.facts['balance']}."
            ),
        )
