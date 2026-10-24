from __future__ import annotations

from ..base import AccountFeature, Recommendation, Signal
from ..context import AccountContext
from ..registry import register
from ..text import format_eur, format_percent


@register
class WithdrawPressure(AccountFeature):
    code = "withdraw_pressure"
    title = "Может вывести деньги"
    description = "Какая доля баланса доступна к выводу: клиент может забрать деньги и уйти."
    group = "finance"
    default_weight = 10.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        account = ctx.account
        if account.balance <= 0 or account.withdraw_balance <= 0:
            return None
        value = min(account.withdraw_balance / account.balance, 1.0)
        return Signal(
            value=value,
            explanation=(
                f"К выводу доступно {format_eur(account.withdraw_balance)}, "
                f"это {format_percent(value)} баланса"
            ),
            facts={"amount": format_eur(account.withdraw_balance)},
        )

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(
            code=self.code,
            title="Выяснить, не собирается ли клиент вывести деньги",
            detail=(
                f"К выводу доступно {signal.facts['amount']}: "
                "предложить потратить их на продление со скидкой."
            ),
        )
