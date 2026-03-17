# Changelog

All notable changes to **Discord Webhook Uploader** are documented in this file.

This changelog was reconstructed from uploaded source snapshots. It reflects the main functional and UI changes visible in the codebase, while very small visual tweaks may not be listed individually.

## [2.0.5]
### Added
- Introduced a popup-based embed color picker (`EmbedColorPopup`) with:
  - saturation/value area
  - hue slider
  - HEX input
  - live color preview
  - save-on-close behavior
### Changed
- Replaced the previous dialog-based color picker with a lightweight floating popup workflow.
- Moved the **Test Webhook** action into the post customization page.
- Improved live synchronization between the post page color swatch and the popup picker.
### Fixed
- Improved embed color editing reliability and popup state handling.

## [2.0.4]
### Changed
- Reworked the embed color selection flow again, using a Qt color dialog-based implementation.
- Restored the **Test Webhook** action inside the post customization workflow.
### Fixed
- Improved color preview and HEX field synchronization.

## [2.0.3]
### Added
- Added a custom embed color editor with:
  - `ColorSpectrumBox`
  - `HueSlider`
  - manual HSV/HEX synchronization
### Changed
- Replaced the simpler color dialog flow with a more advanced custom picker experience.

## [2.0.2]
### Added
- Added post mode selection with optional embed sending.
- Added embed configuration fields in the post customization page.
- Added stored embed color support in configuration.
- Added `ColorSwatchButton` and the first dedicated embed color dialog.
### Changed
- The uploader can now send either plain text content or a Discord embed with configurable color.

## [2.0.1]
### Added
- Added a full **Post Template Page**.
- Added `post.txt` save/load support from the interface.
- Added template rendering helpers for filename and timestamp placeholders.
- Added automatic save behavior when navigating away from the post editor.
### Changed
- Settings now include a direct entry point for editing the post template.

## [2.0.0]
### Changed
- Version bump and maintenance snapshot following the tray icon redesign work.
- No major visible feature expansion compared with `v1.9.9` in the uploaded source snapshot.

## [1.9.9]
### Added
- Integrated the redesigned animated tray icon directly into the main application.
- Added dedicated drawing routines for:
  - active ring state
  - paused ring state
  - animated sending state
### Changed
- Tray behavior now refreshes dynamically while sending.

## [1.9.8]
### Changed
- Refined the tray exit bubble implementation and sizing.
- Polished the lightweight exit UI shown from the tray interaction flow.

## [1.9.7]
### Added
- Added `TrayExitBubble`, a compact floating exit action shown near the cursor.
### Changed
- Replaced the previous tray exit interaction with a cleaner popup-style action.

## [1.9.6]
### Added
- Added `HomeValueRow` to improve value presentation on the home page.
- Added a dedicated **Send Now** button directly in the main bottom action row.
### Changed
- Refined home page layout and button sizing.
- Improved presentation of the saved webhook and watched folder.
- Continued visual cleanup of the PySide6 interface.

## [1.9.5]
### Added
- Added **Clear Log** / sent-history reset support from settings.
- Added backend helper to wipe the sent file log.
### Changed
- Expanded settings management with safer re-upload workflow support.

## [1.9.4]
### Added
- Added `hide_to_tray()` behavior.
- Added a small reusable edit-card pattern for the home page.
### Changed
- The close action now hides the window to the tray instead of exiting immediately.
- Updated the home page to use styled text buttons and card-style editing actions.
- Refined settings button sizing and placement.

## [1.9.3]
### Changed
- Adjusted configuration folder handling and related settings-page details.
- Continued polish of the multi-page PySide6 settings layout.

## [1.9.2]
### Added
- Added a scrollable settings area using `QScrollArea`.
### Changed
- Improved settings page scalability for a growing number of options.

## [1.9.1]
### Added
- Added a dedicated **Browse Folder** action in the folder page.
### Changed
- Improved folder editing workflow in the PySide6 interface.

## [1.9.0]
### Added
- Introduced a larger multi-page PySide6 application structure with:
  - `HomePage`
  - `WebhookPage`
  - `FolderPage`
  - `SettingsPage`
- Added `ToggleSwitch` and richer reusable UI building blocks.
- Added **Start with Windows** support via the Windows registry.
- Added **Test Webhook** support.
- Added normalized configuration handling.
### Changed
- Reworked the application into a more complete desktop UI instead of a single compact window.
- Improved separation between monitoring logic, settings, and navigation.

## [1.8.3]
### Added
- Added `WebhookInputDialog`.
- Added explicit Discord webhook validation.
- Added dialog-level error feedback and clipboard paste support.
### Changed
- Improved webhook editing flow and validation UX.

## [1.8.2]
### Added
- Added better failed-upload handling with an `ERROR` folder workflow.
- Added a direct **Send Now** trigger from the PySide6 interface.
### Changed
- Refined the new PySide6 window layout and bottom action buttons.
- Improved operational handling for watched-folder errors.

## [1.8.0]
### Added
- Major migration to **PySide6**.
- Added a new desktop UI architecture with:
  - `UISignals`
  - `HoverButton`
  - `RoundedPanel`
  - `WebhookWindow`
  - `TrayController`
- Added a modern card-based window layout and richer tray integration.
### Changed
- Replaced the previous Tk / CustomTkinter approach with a more advanced PySide6 desktop interface.

## [1.7.3]
### Added
- Added hidden-root Tkinter utility flow for dialogs and first-run setup.
- Added `send_lock` protection for manual sending and monitoring coordination.
- Added clearer actions for folder change, webhook change, history clearing, and config folder access.
### Changed
- Reworked the interface flow again for stability after the floating menu approach.
- Improved first-run setup and queue-driven GUI actions.

## [1.7.2]
### Changed
- Refined the CustomTkinter floating menu presentation.
- Improved first-run webhook setup flow.
- Tightened send/manual-send behavior and menu interactions.

## [1.7.1]
### Changed
- Maintenance iteration over the CustomTkinter branch.
- Minor refinements to the floating menu-based workflow.

## [1.6]
### Added
- Added `post.txt` template loading support.
### Changed
- Introduced editable post-template behavior into the upload flow.

## [1.5]
### Added
- Migrated the interface to **CustomTkinter**.
- Added a themed floating control menu (`FloatingMenu`).
- Added actions for pause, send, folder selection, webhook editing, history clearing, and exit.
### Changed
- Reworked the project from simple Tkinter dialogs into a more visually styled desktop control panel.

## [1.4]
### Added
- Added a standalone configuration GUI.
- Added **Send Now** support.
- Added cache/history clearing support.
### Changed
- Replaced the earlier setup-only dialog flow with a more configurable control workflow.

## [1.3]
### Added
- Added the ability to change the watched folder after setup.
- Added the ability to change the webhook after setup.

## [1.2]
### Added
- Added file hashing for duplicate detection.
### Changed
- Improved protection against repeated uploads of the same file.

## [1.1]
### Added
- Added JSON-based config persistence in `LOCALAPPDATA`.
- Added a first-run GUI setup flow.
- Added tray image creation and pause/exit actions.
### Changed
- Replaced hardcoded setup with stored configuration files.

## [1.0]
### Added
- Initial working version of the uploader.
- Added watched-folder scanning.
- Added Discord webhook file upload.
- Added tray controls for pause, resume, and exit.
- Added optional delete-after-send behavior using the recycle bin.
