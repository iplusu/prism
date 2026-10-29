FROM node:24.11.1-bookworm-slim
WORKDIR /app
COPY collab/package.json collab/package-lock.json ./
RUN npm ci --omit=dev
COPY collab/server.mjs ./
RUN mkdir -p /srv/prism/collab && chown -R node:node /srv/prism
USER node
ENV COLLAB_HOST=0.0.0.0 COLLAB_PORT=1234 COLLAB_DATA_DIR=/srv/prism/collab
EXPOSE 1234
CMD ["node", "server.mjs"]
