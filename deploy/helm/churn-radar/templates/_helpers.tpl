{{- define "cr.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cr.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "cr.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cr.labels" -}}
helm.sh/chart: {{ include "cr.chart" . }}
app.kubernetes.io/part-of: {{ include "cr.name" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* labels for objects without a component */}}
{{- define "cr.metaLabels" -}}
{{ include "cr.labels" . }}
app.kubernetes.io/name: {{ include "cr.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* selector labels for a component: (dict "root" $ "component" "auth") */}}
{{- define "cr.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cr.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "cr.componentName" -}}
{{- printf "%s-%s" (include "cr.fullname" .root) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* app keys in values -> kubernetes component names */}}
{{- define "cr.appKeys" -}}
auth notifier web api ingest scoring simulator alerts mockCore
{{- end -}}

{{- define "cr.appEnabled" -}}
{{- $root := .root -}}
{{- $key := .key -}}
{{- $app := index $root.Values $key -}}
{{- if and $app.enabled (or (not (has $key (list "api" "ingest" "simulator"))) (not $root.Values.mockCore.enabled)) -}}
true
{{- end -}}
{{- end -}}

{{/* merged app config: (dict "root" $ "key" "auth") -> yaml */}}
{{- define "cr.appConfig" -}}
{{- $root := .root -}}
{{- $merged := mergeOverwrite (deepCopy $root.Values.appDefaults) (deepCopy (index $root.Values .key)) -}}
{{- toYaml $merged -}}
{{- end -}}

{{/* service name that serves a gateway route group */}}
{{- define "cr.backend" -}}
{{- $root := .root -}}
{{- if and $root.Values.mockCore.enabled (has .key (list "api" "ingest" "simulator")) -}}
{{- include "cr.componentName" (dict "root" $root "component" "mock-core") -}}
{{- else -}}
{{- include "cr.componentName" (dict "root" $root "component" (kebabcase .key)) -}}
{{- end -}}
{{- end -}}

{{- define "cr.image" -}}
{{- $root := .root -}}
{{- $img := .image -}}
{{- $registry := default $root.Values.global.image.registry $img.registry -}}
{{- $tag := default (default $root.Chart.AppVersion $root.Values.global.image.tag) $img.tag -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $img.repository $tag -}}
{{- else -}}
{{- printf "%s:%s" $img.repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "cr.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "cr.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "cr.secretName" -}}
{{- default (printf "%s-secrets" (include "cr.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{/* Secret с каналами сервиса оповещений (webhook, Telegram) */}}
{{- define "cr.alertsSecretName" -}}
{{- default (printf "%s-alerts" (include "cr.fullname" .)) .Values.secrets.alerts.existingSecret -}}
{{- end -}}

{{/* Secret с токенами коннекторов-источников (CONNECTOR_TOKEN_*) */}}
{{- define "cr.connectorsSecretName" -}}
{{- default (printf "%s-connectors" (include "cr.fullname" .)) .Values.secrets.connectors.existingSecret -}}
{{- end -}}

{{/* Коннекторы (pull): адреса источников для ingest, демо-токены для симулятора. Токены ingest — в cr.connectorsSecretName */}}
{{- define "cr.connectorsEnv" -}}
{{- $root := .root -}}
{{- $c := $root.Values.connectors -}}
{{- $sim := printf "http://%s:8000" (include "cr.backend" (dict "root" $root "key" "simulator")) -}}
{{- $demo := and $c.demo $root.Values.simulator.enabled -}}
{{- if eq .key "ingest" -}}
{{- $billing := $c.billingUrl | default (ternary (printf "%s/demo/billing/v1" $sim) "" $demo) -}}
{{- $metrica := $c.metricaUrl | default (ternary (printf "%s/demo/metrica" $sim) "" $demo) -}}
{{- $counter := toString ($c.metricaCounter | default (ternary "100500" "" $demo)) -}}
CONNECTOR_BILLING_URL: {{ $billing | quote }}
CONNECTOR_METRICA_URL: {{ $metrica | quote }}
CONNECTOR_METRICA_COUNTER: {{ $counter | quote }}
CONNECTOR_ALLOWED_HOSTS: {{ $c.allowedHosts | quote }}
{{- else if $demo }}
DEMO_BILLING_TOKEN: {{ $root.Values.secrets.connectors.billingToken | quote }}
DEMO_METRICA_TOKEN: {{ $root.Values.secrets.connectors.metricaToken | quote }}
DEMO_METRICA_COUNTER: {{ toString ($c.metricaCounter | default "100500") | quote }}
{{- end -}}
{{- end -}}

{{/* Secret с секретом OIDC-клиента внешнего IdP */}}
{{- define "cr.ssoSecretName" -}}
{{- default (printf "%s-sso" (include "cr.fullname" .)) .Values.sso.existingSecret -}}
{{- end -}}

{{/* переменные SSO для auth (ConfigMap); секрет клиента — в cr.ssoSecretName */}}
{{- define "cr.ssoEnv" -}}
{{- $sso := .Values.sso -}}
OIDC_ENABLED: {{ $sso.oidc.enabled | toString | quote }}
SAML_ENABLED: {{ $sso.saml.enabled | toString | quote }}
SSO_ADMIN_GROUPS: {{ $sso.adminGroups | quote }}
SSO_MARKETER_GROUPS: {{ $sso.marketerGroups | quote }}
SSO_ANALYST_GROUPS: {{ $sso.analystGroups | quote }}
SSO_DEFAULT_ROLE: {{ $sso.defaultRole | quote }}
{{- if $sso.oidc.enabled }}
OIDC_PUBLIC_ISSUER: {{ required "sso.oidc.issuer обязателен при sso.oidc.enabled" $sso.oidc.issuer | quote }}
OIDC_INTERNAL_ISSUER: {{ default $sso.oidc.issuer $sso.oidc.internalIssuer | quote }}
OIDC_CLIENT_ID: {{ $sso.oidc.clientId | quote }}
OIDC_SCOPES: {{ $sso.oidc.scopes | quote }}
OIDC_DISPLAY_NAME: {{ $sso.oidc.displayName | quote }}
{{- end }}
{{- if $sso.saml.enabled }}
{{- if not (or $sso.saml.idpMetadataUrl $sso.saml.idpMetadataXml) }}
{{- fail "sso.saml: нужен idpMetadataUrl или idpMetadataXml" }}
{{- end }}
SAML_IDP_METADATA_URL: {{ $sso.saml.idpMetadataUrl | quote }}
SAML_IDP_METADATA_XML: {{ $sso.saml.idpMetadataXml | quote }}
SAML_IDP_PUBLIC_URL: {{ $sso.saml.idpPublicUrl | quote }}
SAML_SP_ENTITY_ID: {{ $sso.saml.spEntityId | quote }}
SAML_ROLES_ATTRIBUTE: {{ $sso.saml.rolesAttribute | quote }}
SAML_GROUPS_ATTRIBUTE: {{ $sso.saml.groupsAttribute | quote }}
SAML_DISPLAY_NAME: {{ $sso.saml.displayName | quote }}
{{- end }}
{{- end -}}

{{- define "cr.postgresHost" -}}
{{- include "cr.componentName" (dict "root" . "component" "postgresql") -}}
{{- end -}}

{{- define "cr.databaseUrl" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "postgresql://%s:%s@%s:5432/%s" (.Values.postgresql.username | urlquery) (.Values.postgresql.password | urlquery) (include "cr.postgresHost" .) .Values.postgresql.database -}}
{{- else -}}
{{- required "postgresql.external.url is required when postgresql.enabled=false" .Values.postgresql.external.url -}}
{{- end -}}
{{- end -}}

{{- define "cr.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://%s:6379/%v" (include "cr.componentName" (dict "root" . "component" "redis")) .Values.redis.database -}}
{{- else -}}
{{- required "redis.external.url is required when redis.enabled=false" .Values.redis.external.url -}}
{{- end -}}
{{- end -}}

{{- define "cr.natsUrl" -}}
{{- if .Values.nats.enabled -}}
{{- printf "nats://%s:4222" (include "cr.componentName" (dict "root" . "component" "nats")) -}}
{{- else -}}
{{- required "nats.external.url is required when nats.enabled=false" .Values.nats.external.url -}}
{{- end -}}
{{- end -}}

{{- define "cr.svcFqdn" -}}
{{- printf "%s.%s.svc.%s" .name .root.Release.Namespace .root.Values.global.clusterDomain -}}
{{- end -}}

{{/* labels marking pods allowed to reach postgres/redis/nats */}}
{{- define "cr.appTierLabel" -}}
churn-radar/tier: app
{{- end -}}

{{- define "cr.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
fsGroup: {{ .uid }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "cr.containerSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
privileged: false
capabilities:
  drop: ["ALL"]
{{- end -}}

{{/* env from shared secret: (dict "root" $ "keys" list) */}}
{{- define "cr.secretEnv" -}}
{{- $root := .root -}}
{{- range .keys }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ include "cr.secretName" $root }}
      key: {{ . }}
{{- end }}
{{- end -}}

{{- define "cr.storageClass" -}}
{{- if . }}
storageClassName: {{ . | quote }}
{{- end }}
{{- end -}}

{{/* nats.conf: (dict "root" $ "name" "<server name>") */}}
{{- define "cr.natsConfig" -}}
{{- $js := .root.Values.nats.jetstream -}}
server_name: {{ .name | quote }}
port: 4222
http_port: 8222
jetstream {
  store_dir: "/data"
  {{- with $js.maxMemory }}
  max_memory_store: {{ . }}
  {{- end }}
  {{- with $js.maxFile }}
  max_file_store: {{ . }}
  {{- end }}
}
{{- end -}}

{{/* priorityClassName: (dict "root" $ "value" "...") — своё значение или global */}}
{{- define "cr.priorityClassName" -}}
{{- $v := default .root.Values.global.priorityClassName .value -}}
{{- if $v -}}
priorityClassName: {{ $v | quote }}
{{- end -}}
{{- end -}}
