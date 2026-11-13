// Target2Plan desktop app: the web app (../../docs) in a native window. The
// dialog and fs plugins give it native Save dialogs and write the file the
// user chose; the opener plugin opens links in the system browser.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running Target2Plan");
}
