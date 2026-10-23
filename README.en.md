<div align="center">

# ChurnRadar

**Churn radar: which hosting customers may leave, why, and how much revenue is at stake**

[Русская версия](README.md)

</div>

> Work in progress at the RND hackathon. Full run instructions will appear once the first
> working `docker compose up` lands.

ChurnRadar scores every customer's churn risk from services, balances and payments,
explains the reason in plain words and estimates revenue at risk over the forecast horizon.

Event-driven microservices on NATS JetStream, Traefik gateway, PostgreSQL, Redis, React
dashboard, Prometheus and Grafana, password or OIDC (Keycloak) sign-in. Runs with
Docker Compose, deploys to Kubernetes with Helm. API and event contracts: [`contracts/`](contracts/).
