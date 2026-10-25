// Storage is optional: this public site does not currently bind a D1 database.
declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
  }
}
