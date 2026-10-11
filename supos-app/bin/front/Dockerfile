# FactoryMind AI web — image for the open supOS app package.
#
# Build context is this directory (`bin/front`), which the packaging script
# stages with the pnpm workspace subset it needs:
#
#   bin/front/
#   ├── pnpm-workspace.yaml, package.json, pnpm-lock.yaml
#   ├── packages/shared-types/
#   ├── apps/web/
#   └── Dockerfile, nginx.conf
#
#   docker build -t factorymind-ai-front:1.0.0 bin/front
#
# Stage 1 builds the SPA; stage 2 serves it and proxies `/api` to the engine.
FROM node:22-alpine AS build

ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV COREPACK_NPM_REGISTRY=${NPM_REGISTRY} \
    npm_config_registry=${NPM_REGISTRY}

RUN corepack enable

WORKDIR /repo

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY packages/shared-types/package.json packages/shared-types/
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile

COPY packages/shared-types packages/shared-types
COPY apps/web apps/web
RUN pnpm --filter @factorymind/web build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 80
