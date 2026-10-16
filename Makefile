# Optional shortcuts. Everything here is also documented as plain commands in
# the README, so teammates without `make` (e.g. plain Windows) lose nothing.

.PHONY: help env up down logs ps reset k8s-secret k8s-up k8s-down k8s-status

help:
	@echo "Docker Compose:"
	@echo "  make env         create .env from .env.example with a random DB password"
	@echo "  make up          build images and start everything in the background"
	@echo "  make down        stop and remove containers (database data is kept)"
	@echo "  make logs        follow logs of all services"
	@echo "  make ps          show service status and health"
	@echo "  make reset       stop everything AND delete the database volume"
	@echo "Kubernetes (images must already be loaded into your cluster, see README):"
	@echo "  make k8s-secret  create the DB Secret from .env"
	@echo "  make k8s-up      create the Secret and apply all manifests"
	@echo "  make k8s-status  show pods and services"
	@echo "  make k8s-down    delete everything in the smart-locker namespace"

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

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

reset:
	docker compose down -v

k8s-secret:
	sh kubernetes/create-secret.sh

k8s-up: k8s-secret
	kubectl apply -k kubernetes/

k8s-status:
	kubectl -n smart-locker get pods,svc,pvc

k8s-down:
	kubectl delete namespace smart-locker
