# local-transcriber on Kubernetes: one-command targets.
# CPU mode on Docker Desktop Kubernetes (context docker-desktop) or kind.
#
#   make k8s-up     build the image, install KEDA, install the chart
#   make demo       synthesize sample audio and submit jobs, watch scale-from-zero
#   make status     show pods + the KEDA ScaledObject
#   make k8s-down   uninstall the chart and its PVCs

IMAGE     ?= local-transcriber:dev
RELEASE   ?= lt
NAMESPACE ?= default
CHART     ?= deploy/helm/local-transcriber
JOBS      ?= 5
MODEL     ?= tiny

.PHONY: image keda-install chart k8s-up demo status logs k8s-down keda-uninstall

## Build the CPU image into the local Docker (Docker Desktop K8s reuses it).
image:
	docker build -t $(IMAGE) -f Dockerfile .

## For kind clusters only: load the image into the cluster.
kind-load:
	kind load docker-image $(IMAGE)

## Install KEDA into its own namespace (idempotent).
keda-install:
	helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
	helm repo update kedacore
	helm upgrade --install keda kedacore/keda --namespace keda --create-namespace --wait

## Install or upgrade the chart.
chart:
	helm upgrade --install $(RELEASE) $(CHART) --namespace $(NAMESPACE) --wait

## Full bring-up: image + KEDA + chart.
k8s-up: image keda-install chart
	@echo ""
	@echo "Installed. Workers are at zero. Run 'make demo' to submit sample jobs."

## Synthesize sample audio, submit jobs, and poll (port-forwards the API itself).
demo:
	python scripts/demo.py --release $(RELEASE) --namespace $(NAMESPACE) --jobs $(JOBS) --model $(MODEL)

## Show pods and the KEDA ScaledObject.
status:
	kubectl get pods,scaledobject,hpa -l app.kubernetes.io/instance=$(RELEASE) -n $(NAMESPACE)

## Tail worker logs.
logs:
	kubectl logs -l app.kubernetes.io/instance=$(RELEASE),app.kubernetes.io/component=worker -n $(NAMESPACE) --tail=100 -f

## Tear down the chart and its PVCs (keeps KEDA).
k8s-down:
	-helm uninstall $(RELEASE) --namespace $(NAMESPACE)
	-kubectl delete pvc -l app.kubernetes.io/instance=$(RELEASE) -n $(NAMESPACE)
	@echo "KEDA left installed. Remove it with 'make keda-uninstall'."

## Remove KEDA.
keda-uninstall:
	-helm uninstall keda --namespace keda
