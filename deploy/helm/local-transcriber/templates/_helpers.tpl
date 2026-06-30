{{/*
Name helpers for the local-transcriber chart.
*/}}

{{- define "lt.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "lt.fullname" -}}
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

{{- define "lt.labels" -}}
app.kubernetes.io/name: {{ include "lt.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* Component-specific names */}}
{{- define "lt.api.fullname" -}}{{ include "lt.fullname" . }}-api{{- end -}}
{{- define "lt.worker.fullname" -}}{{ include "lt.fullname" . }}-worker{{- end -}}
{{- define "lt.redis.fullname" -}}{{ include "lt.fullname" . }}-redis{{- end -}}

{{/* Env block shared by api and worker (Redis + storage + model cache) */}}
{{- define "lt.commonEnv" -}}
- name: REDIS_HOST
  value: {{ include "lt.redis.fullname" . | quote }}
- name: REDIS_PORT
  value: {{ .Values.redis.port | quote }}
- name: SHARED_DIR
  value: {{ .Values.env.sharedDir | quote }}
- name: HF_HOME
  value: {{ .Values.env.hfHome | quote }}
- name: HF_HUB_DISABLE_TELEMETRY
  value: "1"
- name: PYTHONUNBUFFERED
  value: "1"
{{- end -}}
