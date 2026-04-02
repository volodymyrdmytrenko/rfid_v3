CREATE DATABASE IF NOT EXISTS canteen
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE canteen;

CREATE TABLE IF NOT EXISTS employees (
    id INT PRIMARY KEY,
    rfid VARCHAR(64),
    full_name VARCHAR(100) NOT NULL,
    fmoney INT NOT NULL DEFAULT 50,
    active TINYINT(1) NOT NULL DEFAULT 1,
    updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_full_name (full_name),
    UNIQUE KEY ux_employees_rfid (rfid)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS visits (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    employee_id INT NOT NULL,
    visit_time DATETIME NOT NULL,
    source VARCHAR(20) NOT NULL,
    sync_uuid CHAR(36) NOT NULL,
    FOREIGN KEY (employee_id) REFERENCES employees(id),
    INDEX idx_employee_id (employee_id),
    INDEX idx_visit_time (visit_time),
    UNIQUE KEY ux_visits_sync_uuid (sync_uuid)
) ENGINE=InnoDB;