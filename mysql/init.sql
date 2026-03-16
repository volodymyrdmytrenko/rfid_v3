-- ======================================================
--  База даних їдальні (canteen)
-- ======================================================

CREATE DATABASE IF NOT EXISTS canteen
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE canteen;

-- ======================================================
--  Таблиця співробітників
-- ======================================================

CREATE TABLE IF NOT EXISTS employees (
	id INT AUTO_INCREMENT PRIMARY KEY,
    rfid VARCHAR(64),
    full_name VARCHAR(100) not null,
    active TINYINT(1) DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
	
	INDEX idx_full_name (full_name)
)ENGINE=InnoDB;


-- ======================================================
--  Таблиця візитів
-- ======================================================

CREATE TABLE IF NOT EXISTS visits (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    employee_id INT not null,
    visit_time DATETIME,
    source VARCHAR(20),
    FOREIGN KEY (employee_id) REFERENCES employees(id),
	
	INDEX idx_employee_id (employee_id),
	INDEX idx_visit_time (visit_time)
)ENGINE=InnoDB;


-- ======================================================
--  Рекомендований користувач БД
-- ======================================================

-- CREATE USER 'canteen'@'%' IDENTIFIED BY 'GNgfvPeRNX0c5n';
-- GRANT SELECT, INSERT, UPDATE ON canteen.* TO 'canteen'@'%';
-- FLUSH PRIVILEGES;

