# Optional shortcuts. Everything here is also documented as plain commands in
# the README, so teammates without `make` (e.g. plain Windows) lose nothing.

IMAGES = smart-locker-backend:local smart-locker-frontend:local smart-locker-controller:local

.PHONY: help env up down logs ps reset monitoring proto test \
        k8s-addons k8s-images k8s-secret k8s-up k8s-status k8s-down \
        k8s-loadtest k8s-failover k8s-grafana k8s-prometheus k8s-app

help:
	@echo "Docker Compose:"
	@echo "  make env          create .env from .env.example with a random DB password"
	@echo "  make up           build images and start everything in the background"
	@echo "  make monitoring   same, plus Prometheus (:9090) and Grafana (:3001)"
	@echo "  make down         stop and remove containers (database data is kept)"
	@echo "  make logs / ps    follow logs / show service status"
	@echo "  make reset        stop everything AND delete the database volume"
	@echo "  make test         run the API tests against the running stack"
	@echo "  make proto        generate gRPC stubs locally (only for running outside Docker)"
	@echo "Kubernetes:"
	@echo "  make k8s-addons   install metrics-server + CloudNativePG (once per cluster)"
	@echo "  make k8s-images   build images and load them into minikube / kind"
	@echo "  make k8s-up       create the Secrets and apply all manifests"
	@echo "  make k8s-status   show pods, autoscaler and database cluster"
	@echo "  make k8s-app      open the app on http://localhost:8080"
	@echo "  make k8s-grafana  open Grafana on http://localhost:3001"
	@echo "  make k8s-loadtest run the k6 load test inside the cluster"
	@echo "  make k8s-failover kill the database primary and watch a replica take over"
	@echo "  make k8s-down     delete everything in the smart-locker namespace"

env:
	@if [ -f .env ]; then \
	  echo ".env already exists - not overwriting it"; \
	else \
	  pw=$$(openssl rand -hex 16) && \
	  sed "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$pw/" .env.example > .env && \
	  echo "Created .env with a random database password"; \
	fi

up:
	docker compose up --build -d

monitoring:
	docker compose --profile monitoring up --build -d

down:
	docker compose --profile monitoring down

logs:
	docker compose logs -f

ps:
	docker compose ps

reset:
	docker compose --profile monitoring down -v

test:
	python -m pytest tests/test_init.py tests/test_end_to_end.py -v

proto:
	python -m grpc_tools.protoc -I proto --python_out=backend --grpc_python_out=backend --pyi_out=backend proto/smartlocker/v1/locker_controller.proto
	python -m grpc_tools.protoc -I proto --python_out=locker-controller --grpc_python_out=locker-controller --pyi_out=locker-controller proto/smartlocker/v1/locker_controller.proto

k8s-addons:
	sh kubernetes/addons/install.sh

# Builds with Compose, then copies the images into the cluster's nodes.
k8s-images:
	docker compose build backend frontend locker-controller
	@ctx=$$(kubectl config current-context); \
	case "$$ctx" in \
	  kind-*) for i in $(IMAGES); do kind load docker-image $$i --name $${ctx#kind-}; done ;; \
	  docker-desktop) echo "Docker Desktop Kubernetes uses local images directly; nothing to load." ;; \
	  *) for i in $(IMAGES); do echo "loading $$i into minikube profile $$ctx"; minikube -p $$ctx image load $$i; done ;; \
	esac

k8s-secret:
	sh kubernetes/create-secret.sh

k8s-up: k8s-secret
	kubectl apply -k .

k8s-status:
	kubectl -n smart-locker get pods -o wide
	kubectl -n smart-locker get hpa,cluster,pooler

k8s-app:
	kubectl -n smart-locker port-forward svc/frontend 8080:80

k8s-grafana:
	kubectl -n smart-locker port-forward svc/grafana 3001:3000

k8s-prometheus:
	kubectl -n smart-locker port-forward svc/prometheus 9090:9090

k8s-loadtest:
	kubectl -n smart-locker delete job k6-load-test --ignore-not-found
	kubectl apply -k kubernetes/loadtest
	kubectl -n smart-locker wait --for=condition=ready pod -l app=k6 --timeout=120s
	kubectl -n smart-locker logs -f job/k6-load-test

k8s-failover:
	@primary=$$(kubectl -n smart-locker get cluster locker-db -o jsonpath='{.status.currentPrimary}'); \
	echo "Current primary: $$primary - deleting it"; \
	kubectl -n smart-locker delete pod $$primary --wait=false; \
	echo "Watching instances (Ctrl+C to stop). A replica becomes primary within seconds:"; \
	kubectl -n smart-locker get pods -l cnpg.io/cluster=locker-db -L role -w

k8s-down:
	kubectl delete namespace smart-locker
