CREATE TABLE customers (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE vehicles (
  id INTEGER PRIMARY KEY,
  make TEXT NOT NULL,
  model TEXT NOT NULL,
  year INTEGER NOT NULL,
  daily_rate REAL NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE rentals (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL,
  vehicle_id INTEGER NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  status TEXT NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id),
  FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

CREATE TABLE payments (
  id INTEGER PRIMARY KEY,
  rental_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  paid_at TEXT,
  status TEXT NOT NULL,
  FOREIGN KEY (rental_id) REFERENCES rentals(id)
);

CREATE TABLE audit_logs (
  id INTEGER PRIMARY KEY,
  event TEXT NOT NULL,
  actor TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  details TEXT
);
