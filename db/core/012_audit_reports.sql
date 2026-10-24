-- Единый журнал действий и расписание еженедельного отчёта.
--
-- audit.events — один журнал на всю систему: вход и выход, пользователи и API-токены (auth),
-- модель, данные, письма, рассылки, настройки (api, ingest, alerts). Каждый сервис пишет
-- сюда сам, в ту же базу, что и данные: без очередей посередине запись не теряется, пока жива
-- база. Читает журнал api (страница «Журнал действий», выгрузка CSV) и auth (журнал
-- пользователей на странице «Пользователи»).
--
-- Раньше у auth были свои auth.audit_log и auth.login_events. Их записи переносятся сюда,
-- новые туда больше не пишутся.
-- IF NOT EXISTS: файл можно прогнать повторно (так делают тесты других пакетов).

CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           timestamptz(3) NOT NULL DEFAULT now(),
    service      text NOT NULL,                  -- кто записал: auth, api, ingest, alerts
    -- кто сделал: логин сотрудника, «token:<имя>» для API-токена, system для фоновых задач
    actor        text NOT NULL,
    actor_type   text NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user', 'token', 'system', 'anonymous')),
    actor_role   text,
    action       text NOT NULL,                  -- auth.login, user.create, token.revoke, model.settings.update…
    object_type  text,                           -- user, token, customer, model, source, mail_template…
    object_id    text,
    success      boolean NOT NULL DEFAULT true,  -- false: неудачный вход, отказ в доступе
    ip           text,
    request_id   text,
    details      jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS events_at_idx ON audit.events (at DESC, id DESC);
CREATE INDEX IF NOT EXISTS events_action_idx ON audit.events (action, at DESC);
CREATE INDEX IF NOT EXISTS events_actor_idx ON audit.events (lower(actor), at DESC);
CREATE INDEX IF NOT EXISTS events_object_idx ON audit.events (object_type, object_id);

-- Перенос старых журналов auth. Таблиц может не быть (чистая база, миграции auth ещё идут) —
-- тогда переносить нечего. Повторный прогон ничего не дублирует: переносим, только пока
-- от auth в журнале нет ни одной записи.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM audit.events WHERE service = 'auth') THEN
        RETURN;
    END IF;
    IF to_regclass('auth.audit_log') IS NOT NULL THEN
        INSERT INTO audit.events (at, service, actor, actor_type, action, object_type, object_id, details)
        SELECT created_at, 'auth', actor, 'user', action,
               CASE WHEN action LIKE 'self.%' OR action LIKE 'user.%' THEN 'user' END, target, details
        FROM auth.audit_log ORDER BY id;
    END IF;
    IF to_regclass('auth.login_events') IS NOT NULL THEN
        INSERT INTO audit.events (at, service, actor, actor_type, action, object_type, object_id, success, ip, details)
        SELECT created_at, 'auth', username, CASE WHEN success THEN 'user' ELSE 'anonymous' END,
               CASE WHEN success THEN 'auth.login' ELSE 'auth.login_failed' END,
               'user', username, success, ip, jsonb_build_object('method', provider)
        FROM auth.login_events ORDER BY id;
    END IF;
END
$$;

-- Еженедельный отчёт руководству: кому и когда слать. Одна строка, пишет api (роль admin).
-- last_sent_on — неделя уже отправлена; по нему реплики api не шлют отчёт дважды.
CREATE TABLE IF NOT EXISTS scoring.report_settings (
    id            smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled       boolean NOT NULL DEFAULT false,
    weekday       smallint NOT NULL DEFAULT 1 CHECK (weekday BETWEEN 1 AND 7),  -- ISO: 1 — понедельник
    send_time     time NOT NULL DEFAULT '09:00',
    timezone      text NOT NULL DEFAULT 'Europe/Moscow',
    recipients    text[] NOT NULL DEFAULT '{}',
    last_sent_on  date,
    last_sent_at  timestamptz(3),
    last_error    text,
    updated_at    timestamptz(3) NOT NULL DEFAULT now(),
    updated_by    text
);
