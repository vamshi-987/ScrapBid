FROM node:26-trixie-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072

WORKDIR /app

# Source and node_modules are mounted by Compose; the entrypoint installs
# dependencies into the node_modules volume whenever package-lock.json changes.
COPY docker-entrypoint.sh /usr/local/bin/frontend-entrypoint.sh
# A new node_modules volume copies this directory's owner, so npm can write to it.
RUN mkdir -p /app/node_modules && chown -R node:node /app
USER node

EXPOSE 5173
ENTRYPOINT ["frontend-entrypoint.sh"]
CMD ["npm", "run", "dev"]
