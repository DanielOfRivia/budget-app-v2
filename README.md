# 📊 Monthly Budget Automation App

An interactive Streamlit web application designed to completely automate your monthly financial workflows. Instead of manually sorting through bank statements, this app allows you to upload raw credit card data, leverages the **Gemini API** for intelligent expense categorization, gives you full editing control, and seamlessly syncs everything directly to **Google Drive** and **Google Sheets**.

---

## 🚀 Features

* **Multi-Statement Upload:** Drop in raw transaction CSV statements from your financial institutions (e.g., RBC).
* **Data Consolidation:** Automatically parses and merges varying statement schemas into a single unified table.
* **AI-Powered Categorization:** Utilizes the Gemini API to intelligently read transaction descriptions and assign accurate budget categories.
* **Interactive Overrides:** Review the AI's assignments inside an editable Streamlit data table and tweak categories on the fly before saving.
* **Unified Google Ecosystem Storage:**
    * **Google Drive:** Saves a physical copy of the finalized `.csv` file into specific backup folders.
    * **Google Sheets:** Directly appends the new transaction rows to your centralized tracking spreadsheet (e.g., `Personal_Budget_2026`) for live budgeting dashboards.

---

## 🛠️ Architecture & Tech Stack

* **Frontend/Interface:** [Streamlit](https://streamlit.io/)
* **Data Processing:** [Pandas](https://pandas.pydata.org/)
* **Artificial Intelligence:** [Google Gemini API](https://ai.google.dev/)
* **Cloud Storage APIs:** [Google Drive API v3](https://developers.google.com/drive) & [gspread](https://gspread.org/) (via a unified OAuth 2.0 User Flow)

---