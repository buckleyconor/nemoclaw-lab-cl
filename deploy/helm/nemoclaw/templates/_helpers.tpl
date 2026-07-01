{{/*
NemoClaw Helm chart helpers.
*/}}

{{- define "nemoclaw.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "nemoclaw.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "nemoclaw.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Standard labels applied to every resource */}}
{{- define "nemoclaw.labels" -}}
helm.sh/chart: {{ include "nemoclaw.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{ include "nemoclaw.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/* Selector labels — used in Service.selector and Deployment.matchLabels */}}
{{- define "nemoclaw.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nemoclaw.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* ServiceAccount name */}}
{{- define "nemoclaw.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "nemoclaw.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Render a fully-qualified image reference: registry/image:tag */}}
{{- define "nemoclaw.image" -}}
{{- $reg := .root.Values.global.registry -}}
{{- $tag := .root.Values.global.tag -}}
{{- printf "%s/%s:%s" $reg .name $tag -}}
{{- end }}
