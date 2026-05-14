# Multi-stage build: Vite React app → nginx static server (DEPLOY-REQ-011)

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine@sha256:feb6f75a08aa55b44576f98c15b8859819ecf54f3e4d2157f42c2d01cb58a3d2
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
