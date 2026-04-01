# Placeholder frontend — serves a static page via nginx.
# Replaced in Phase 5+ with multi-stage React/Vite build (DEPLOY-REQ-011).

FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY public/ /usr/share/nginx/html/
