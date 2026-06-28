const DATA_ROUTES = {
  '/api/result.json': 'public/result.json',
  '/api/venues.json': 'public/venues.json',
};

function jsonHeaders() {
  return {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'public, max-age=300',
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const key = DATA_ROUTES[url.pathname];

    if (key) {
      const object = await env.DATA_BUCKET.get(key);
      if (!object) return new Response('not found', { status: 404 });
      return new Response(object.body, { headers: jsonHeaders() });
    }

    return env.ASSETS.fetch(request);
  },
};
