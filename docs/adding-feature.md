# Как добавить признак в модель

Признак в ChurnRadar занимает один файл в `libs/riskcore/riskcore/features/`. Больше ничего менять не нужно: схема базы, API, фронтенд и настройки остаются как есть. После пересборки сервисов новый признак сам появляется в настройках модели с весом по умолчанию, участвует в расчёте, показывается в разложении score и в причинах риска, а если у него есть рекомендация, то и в списке действий для маркетолога.

## Как это устроено

Пакет `riskcore.features` при импорте сам находит все модули в своей папке и загружает их. Декоратор `@register` создаёт экземпляр класса и кладёт его в реестр. Функция `merge_settings` берёт сохранённые настройки и дополняет их признаками из реестра, которых там ещё нет, поэтому новый признак попадает в модель без миграций и правок в базе. Настройки, которые уже поменял пользователь, при этом сохраняются.

Признак бывает двух видов. `ServiceFeature` получает `ServiceContext` и считается для каждой действующей услуги: в контексте есть сама услуга, клиент, дни до окончания срока, месячная цена, транзакции этой услуги, деньги клиента (`funds`) и график продлений. `AccountFeature` получает `AccountContext`, считается один раз на клиента и применяется ко всем его действующим услугам: в контексте есть клиент, все его услуги, действующие услуги, транзакции, деньги и горизонт.

Метод `compute` возвращает `Signal(value, explanation)`, где `value` лежит от 0 до 1, а `explanation` — фраза для маркетолога с конкретными цифрами. Если признак не сработал или судить не по чему, нужно вернуть `None`.

## Пример: новый клиент

Допустим, мы заметили, что клиенты чаще уходят в первый месяц, пока не привыкли к сервису. Создаём файл `libs/riskcore/riskcore/features/new_customer.py`:

```python
from __future__ import annotations

from ..base import AccountFeature, Recommendation, Signal
from ..context import AccountContext
from ..registry import register


@register
class NewCustomer(AccountFeature):
    code = "new_customer"
    title = "Новый клиент"
    description = "Клиент с нами меньше 30 дней и ещё не привык к сервису. Сила убывает к 30-му дню."
    group = "behavior"
    default_weight = 20.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        if not ctx.all_services:
            return None
        days = max((ctx.as_of - min(s.created_at for s in ctx.all_services)).days, 0)
        if days >= 30:
            return None
        return Signal(1 - days / 30, f"Клиент с нами всего {days} дн.")

    def recommend(self, signal: Signal) -> Recommendation:
        return Recommendation(self.code, "Провести онбординг", "Позвонить и помочь настроить сервер.")
```

Поля класса означают следующее. `code` — уникальный код, по нему признак хранится в настройках и приходит в API. `title` и `description` видит пользователь в настройках модели. `group` используется для группировки на экране (`renewal`, `finance` или `behavior`). `default_weight` — коэффициент от 0 до 100, с которым признак включится у всех, кто его ещё не настраивал. Метод `recommend` необязателен: если его нет, в списке действий появится общая формулировка с объяснением сигнала.

## Тест

Рядом с остальными тестами в `libs/riskcore/tests/` стоит проверить, что признак срабатывает, когда должен, и молчит в остальных случаях:

```python
from riskcore.registry import get_feature

from .factories import account_ctx, make_service


def test_new_customer():
    feature = get_feature("new_customer")
    fresh = feature.compute(account_ctx(services=[make_service(created_days_ago=6)]))
    assert fresh is not None and abs(fresh.value - 0.8) < 1e-9
    assert feature.compute(account_ctx(services=[make_service(created_days_ago=45)])) is None
```

После этого прогоняем все тесты библиотеки:

```bash
python -m pytest libs/riskcore -q
```

Общие тесты сами проверят, что вклады нового признака сходятся в score, что повышение его веса не уменьшает риск и что ответы API по-прежнему соответствуют `contracts/openapi.yaml`.

## Что произойдёт после выкладки

1. `scoring` при следующем пересчёте вызовет `merge_settings`, и в настройках появится `new_customer` с весом 20.
2. В `GET /api/v1/model/settings` признак придёт вместе с остальными, и его можно будет выключить или поменять вес в интерфейсе.
3. В карточках клиентов он появится в разложении score, а там, где его вклад больше 0.5 пункта, и в причинах риска.
4. В сводке он попадёт в топ причин, если войдёт в тройку главных хотя бы у одного клиента.

Если признак нужно убрать, достаточно удалить файл. Его вес останется в сохранённых настройках, но `merge_settings` его проигнорирует.
