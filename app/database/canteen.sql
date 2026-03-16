-- phpMyAdmin SQL Dump
-- version 5.2.3
-- https://www.phpmyadmin.net/
--
-- Хост: db:3306
-- Час створення: Бер 09 2026 р., 13:07
-- Версія сервера: 8.0.45
-- Версія PHP: 8.3.26

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- База даних: `canteen`
--

-- --------------------------------------------------------

--
-- Структура таблиці `employees`
--
CREATE TABLE `employees` (
  `id` int NOT NULL,
  `rfid` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `full_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `active` tinyint(1) NOT NULL DEFAULT 1,
  `updated_at` datetime DEFAULT NULL,
  `money` smallint NOT NULL DEFAULT 50,
  PRIMARY KEY (`id`),
  KEY `ix_rfid` (`rfid`),
  KEY `ix_active` (`active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Структура таблиці `operators`
--

CREATE TABLE `operators` (
  `id` int NOT NULL,
  `username` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `password_hash` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `active` tinyint(1) DEFAULT '1',
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Дамп даних таблиці `operators`
--

INSERT INTO `operators` (`id`, `username`, `password_hash`, `active`, `updated_at`) VALUES
(1, 'nata', 'scrypt:32768:8:1$5oZRAWYcK7X1Xeoi$447e4fd60dbf96b089378ffa5673d7bb628388bc0918a0a6a6227d4c22c573bb399ed7a3bdd692d223820d3ca38c96c93f27b9297a1d119009171ddeb05aadc1', 1, '2026-03-09 09:54:27'),
(3, 'admin', 'scrypt:32768:8:1$yBKDWskKGb71ICrV$f17bff959cff59cf8f6e25890b53ae0e3cc72daab90f620c29c5c0fd21c5610bdb0a2cbc5271749a5f555b2e2098dc93b0ca087912063999d055e15b9074e888', 1, '2026-03-09 09:57:54');

-- --------------------------------------------------------

--
-- Структура таблиці `visits`
--

CREATE TABLE `visits` (
  `id` bigint NOT NULL,
  `employee_id` int NOT NULL,
  `visit_time` datetime DEFAULT NULL,
  `source` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Індекси збережених таблиць
--

--
-- Індекси таблиці `operators`
--
ALTER TABLE `operators`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `username` (`username`);

--
-- Індекси таблиці `visits`
--
ALTER TABLE `visits`
  ADD PRIMARY KEY (`id`),
  ADD KEY `idx_employee_id` (`employee_id`),
  ADD KEY `idx_visit_time` (`visit_time`);

--
-- AUTO_INCREMENT для збережених таблиць
--

--
-- AUTO_INCREMENT для таблиці `operators`
--
ALTER TABLE `operators`
  MODIFY `id` int NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=5;

--
-- AUTO_INCREMENT для таблиці `visits`
--
ALTER TABLE `visits`
  MODIFY `id` bigint NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=72;

--
-- Обмеження зовнішнього ключа збережених таблиць
--

--
-- Обмеження зовнішнього ключа таблиці `visits`
--
ALTER TABLE `visits`
  ADD CONSTRAINT `visits_ibfk_1` FOREIGN KEY (`employee_id`) REFERENCES `employees` (`id`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
