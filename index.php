<?php
/*  plant_tracker_reworked.php  –  Responsive Seed / Plant log (en/fr)
    -----------------------------------------------------------------
    • Bootstrap 5.3 mobile‑first layout
    • Off‑canvas form for adding / editing plants
    • English / French language switcher (query param or session)
    • Collapsible accordion timeline retained
*/

ini_set('display_errors', 1);
error_reporting(E_ALL);

session_start();

/* ---------- i18n ------------------------------------------------------- */
$AVAILABLE_LANG = ['en', 'fr'];
if (isset($_GET['lang']) && in_array($_GET['lang'], $AVAILABLE_LANG, true)) {
    $_SESSION['lang'] = $_GET['lang'];
}
$lang = $_SESSION['lang'] ?? 'en';

$T = [
    'en' => [
        'Plant Tracker'           => 'Plant Tracker',
        'Add plant'               => 'Add plant',
        'Basic info'              => 'Basic info',
        'Common name'             => 'Common name',
        'Latin name'              => 'Latin name',
        'Current stage'           => 'Current stage',
        'Sow'                     => 'Sow',
        'Soak'                    => 'Soak',
        'Strat'                   => 'Strat',
        'Sprout'                  => 'Sprout',
        'Sow date'                => 'Sow date',
        'Est. sprout time (min)'  => 'Est. sprout time (min)',
        'Est. sprout time (max)'  => 'Est. sprout time (max)',
        'Soak start date'         => 'Soak start date',
        'Soak duration'           => 'Soak duration',
        'Strat start date'        => 'Strat start date',
        'Strat duration'          => 'Strat duration',
        'Sprouted on'             => 'Sprouted on',
        'Save'                    => 'Save',
        'My plants'               => 'My plants',
        'Edit / Add stage'        => 'Edit / Add stage',
        'Language'                => 'Language',
        'English'                 => 'English',
        'French'                  => 'Français',
        'days'                    => 'days',
        'weeks'                   => 'weeks',
        'months'                  => 'months',
        'hours'                   => 'hours',
        'Overdue'                 => 'Overdue',
        'Anytime soon'            => 'Anytime soon',
        'left'                    => 'left',
        'finished'                => 'finished',
        'between'                 => 'between',
        'ends'                    => 'ends',
        'today'                   => 'today',
        'and'                     => 'and',
        'between_dates'           => 'between %s and %s',
        'remaining_range'         => '(%d-%d days left)',
    ],
    'fr' => [
        'Plant Tracker'           => 'Suivi de Plantes',
        'Add plant'               => 'Ajouter une plante',
        'Basic info'              => 'Informations',
        'Common name'             => 'Nom commun',
        'Latin name'              => 'Nom latin',
        'Current stage'           => 'Étape actuelle',
        'Sow'                     => 'Semis',
        'Soak'                    => 'Trempage',
        'Strat'                   => 'Stratification',
        'Sprout'                  => 'Germination',
        'Sow date'                => 'Date de semis',
        'Est. sprout time (min)'  => 'Germination min.',
        'Est. sprout time (max)'  => 'Germination max.',
        'Soak start date'         => 'Début trempage',
        'Soak duration'           => 'Durée trempage',
        'Strat start date'        => 'Début stratification',
        'Strat duration'          => 'Durée stratification',
        'Sprouted on'             => 'Germé le',
        'Save'                    => 'Enregistrer',
        'My plants'               => 'Mes plantes',
        'Edit / Add stage'        => 'Modifier/ajouter étape',
        'Language'                => 'Langue',
        'English'                 => 'Anglais',
        'French'                  => 'Français',
        'days'                    => 'jours',
        'weeks'                   => 'semaines',
        'months'                  => 'mois',
        'hours'                   => 'heures',
        'Overdue'                 => 'En retard',
        'Anytime soon'            => 'Imminent',
        'left'                    => 'restant',
        'finished'                => 'terminé',
        'between'                 => 'entre',
        'ends'                    => 'fin le',
        'today'                   => 'aujourd\'hui',
        'and'                     => 'et',
        'between_dates'           => 'entre %s et %s',
        'remaining_range'         => '(entre %d et %d jours restants)',
    ],
];

function t(string $k): string
{
    global $T, $lang;
    return $T[$lang][$k] ?? $k;
}

/* ---------- config & helpers ------------------------------------------ */

date_default_timezone_set('UTC');

define('DATA_FILE', __DIR__ . '/plants.json');

function load_data(): array
{
    if (!file_exists(DATA_FILE)) {
        file_put_contents(DATA_FILE, '[]');
    }
    $arr = json_decode(file_get_contents(DATA_FILE), true);
    if (json_last_error() !== JSON_ERROR_NONE) {
        throw new RuntimeException(json_last_error_msg());
    }
    return $arr;
}

function save_data(array $plants): void
{
    $json = json_encode($plants, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    if (json_last_error() !== JSON_ERROR_NONE) {
        throw new RuntimeException(json_last_error_msg());
    }
    file_put_contents(DATA_FILE, $json);
}

function duration_to_days(int $val, string $unit): int
{
    return match ($unit) {
        'months' => $val * 30,
        'weeks'  => $val * 7,
        'hours'  => (int)round($val / 24),
        default  => $val,
    };
}

function remaining_until(string $end): string
{
    $now = new DateTime();
    $endD = new DateTime($end);
    if ($endD <= $now) return t('finished');
    $days = $now->diff($endD)->days;
    return $days === 0 ? t('today') : "$days " . t('days') . ' ' . t('left');
}

function e(string $s): string
{
    return htmlspecialchars($s, ENT_QUOTES);
}

/* ---------- load ------------------------------------------------------- */
try {
    $plants = load_data();
} catch (Throwable $ex) {
    die('<pre style="color:red">' . $ex->getMessage() . '</pre>');
}

$errors = [];
$isEdit = false;
$idx = null;

/* ---------- default form ---------------------------------------------- */
$form = [
    'common'       => '',
    'latin'        => '',
    'status'       => 'sow',
    'date_sow'     => '',
    'sprout_min'   => 0, 'sprout_min_u' => 'days',
    'sprout_max'   => 0, 'sprout_max_u' => 'days',
    'soak_date'    => '', 'soak_val' => 0, 'soak_unit' => 'hours',
    'strat_date'   => '', 'strat_val' => 0, 'strat_unit' => 'days',
    'sprout_date'  => '',
    'location'     => '',
    'notes'        => '',
];

/* ---------- handle POST ------------------------------------------------ */
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    foreach ($form as $k => $v) {
        if (isset($_POST[$k])) {
            $form[$k] = is_string($_POST[$k]) ? trim($_POST[$k]) : $_POST[$k];
        }
    }
    $idx    = isset($_POST['idx']) ? (int)$_POST['idx'] : null;
    $isEdit = $idx !== null && isset($plants[$idx]);

    if ($form['common'] === '') $errors[] = t('Common name') . ' required.';
    if ($form['latin'] === '')  $errors[] = t('Latin name') . ' required.';

    $status = $form['status'];
    $action = null;

    switch ($status) {
        case 'sow':
            if ($form['date_sow'] === '') $errors[] = t('Sow date') . ' required.';
            $minDays = duration_to_days((int)$form['sprout_min'], $form['sprout_min_u']);
            $maxDays = duration_to_days((int)$form['sprout_max'], $form['sprout_max_u']);
            if ($minDays <= 0 || $maxDays <= 0)  $errors[] = 'Sprout range must be > 0.';
            if ($minDays > $maxDays)              $errors[] = 'Sprout min > max.';
            $action = [
                'action' => 'sow',
                'start'  => $form['date_sow'],
                'range'  => [
                    (int)$form['sprout_min'], $form['sprout_min_u'],
                    (int)$form['sprout_max'], $form['sprout_max_u'],
                ],
            ];
            break;
        case 'soak':
            if ($form['soak_date'] === '')       $errors[] = t('Soak start date') . ' required.';
            if ((int)$form['soak_val'] <= 0)     $errors[] = 'Duration > 0.';
            $action = [
                'action'   => 'soak',
                'start'    => $form['soak_date'],
                'duration' => [(int)$form['soak_val'], $form['soak_unit']],
            ];
            break;
        case 'strat':
            if ($form['strat_date'] === '')      $errors[] = t('Strat start date') . ' required.';
            if ((int)$form['strat_val'] <= 0)     $errors[] = 'Duration > 0.';
            $action = [
                'action'   => 'strat',
                'start'    => $form['strat_date'],
                'duration' => [(int)$form['strat_val'], $form['strat_unit']],
            ];
            break;
        case 'sprout':
            if ($form['sprout_date'] === '')     $errors[] = t('Sprouted on') . ' required.';
            $action = ['action' => 'sprout', 'start' => $form['sprout_date']];
            break;
        default:
            $errors[] = 'Unknown status.';
    }

    if (!$errors) {
        if ($isEdit) {
            $plant = $plants[$idx];
            $plant['common'] = $form['common'];
            $plant['latin']  = $form['latin'];
            $plant['location'] = $form['location'];
            $plant['notes'] = $form['notes'];
            array_unshift($plant['history'], $action);
            $plants[$idx] = $plant;
        } else {
            $plants[] = [
                'common'  => $form['common'],
                'latin'   => $form['latin'],
                'location'=> $form['location'],
                'notes'   => $form['notes'],
                'history' => [$action],
            ];
        }
        save_data($plants);
        header('Location: ' . strtok($_SERVER['REQUEST_URI'], '?') . '?lang=' . $lang);
        exit;
    }
}

/* ---------- pre‑fill on edit ------------------------------------------ */
if (isset($_GET['edit'])) {
    $idx = (int)$_GET['edit'];
    if (isset($plants[$idx])) {
        $isEdit = true;
        $plant = $plants[$idx];
        $form['common'] = $plant['common'];
        $form['latin']  = $plant['latin'];
        $first = $plant['history'][0];
        $form['status'] = $first['action'];
        $form['location'] = $plant['location'] ?? '';
        $form['notes'] = $plant['notes'] ?? '';
        switch ($first['action']) {
            case 'sow':
                $form['date_sow'] = $first['start'];
                [$min, $minU, $max, $maxU] = $first['range'];
                $form['sprout_min']    = $min;  $form['sprout_min_u'] = $minU;
                $form['sprout_max']    = $max;  $form['sprout_max_u'] = $maxU;
                break;
            case 'soak':
                [$v, $u] = $first['duration'];
                $form['soak_date'] = $first['start'];
                $form['soak_val']  = $v;  $form['soak_unit'] = $u;
                break;
            case 'strat':
                [$v, $u] = $first['duration'];
                $form['strat_date'] = $first['start'];
                $form['strat_val']  = $v;  $form['strat_unit'] = $u;
                break;
            case 'sprout':
                $form['sprout_date'] = $first['start'];
        }
    }
}

/* ---------- icon map --------------------------------------------------- */
$icon = [
    'sow'    => ['fa-seedling',  'text-success'],
    'soak'   => ['fa-tint',      'text-primary'],
    'strat'  => ['fa-snowflake', 'text-info'],
    'sprout' => ['fa-leaf',      'text-success'],
];
?>
<!doctype html>
<html lang="<?= $lang ?>">
<head>
<meta charset="utf-8">
<title>Plantlog - <?= t('Plant Tracker') ?></title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<style>
/* timeline using flex (better mobile) */
.timeline {
  list-style: none;
  padding-left: 0;
  position: relative;
}
.timeline::before {
  content: '';
  position: absolute;
  top: 0.75rem; /* Start at first icon's center */
  /* Fixed height calculation that stops at the last icon */
  bottom: 1.75rem; /* Stop before the last icon's bottom edge */
  left: 0.75rem;
  width: 0.15rem;
  background-color: #dee2e6;
}
.timeline-item {
  display: flex;
  align-items: flex-start;
  gap: .75rem;
  position: relative;
  padding-block: .5rem;
  margin-left: .85rem;
}
.timeline-item:first-child { padding-top: 0; }
.timeline-item:last-child { padding-bottom: 0; }
.timeline-icon {
  width: 1.75rem;
  height: 1.75rem;
  flex: 0 0 auto;
  border: .125rem solid #dee2e6;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  position: relative;
  z-index: 1;
  margin-left: -1rem;
}
@media (min-width: 600px) {
  .timeline-item { gap: 1rem; }
}
</style>
</head>
<body>
<!-- ---------- NAVBAR --------------------------------------------------- -->
<nav class="navbar navbar-expand-lg navbar-light bg-light">
  <div class="container-fluid">
    <a class="navbar-brand" href="?lang=<?= $lang ?>">Plantlog - <?= t('Plant Tracker') ?></a>
    <div class="dropdown ms-auto">
      <button class="btn btn-outline-secondary dropdown-toggle" data-bs-toggle="dropdown">
        <?= t('Language') ?>
      </button>
      <ul class="dropdown-menu dropdown-menu-end">
        <li><a class="dropdown-item" href="?lang=en"><?= t('English') ?></a></li>
        <li><a class="dropdown-item" href="?lang=fr"><?= t('French') ?></a></li>
      </ul>
    </div>
  </div>
</nav>

<div class="container py-4">
<?php if ($errors): ?>
  <div class="alert alert-danger"><ul class="mb-0">
    <?php foreach ($errors as $er) echo '<li>' . e($er) . '</li>'; ?>
  </ul></div>
<?php endif; ?>

<!-- ---------- ADD BUTTON ---------------------------------------------- -->
<button class="btn btn-success mb-3" data-bs-toggle="offcanvas" data-bs-target="#plantFormCanvas" <?= $isEdit ? 'style="display:none"' : '' ?>>
  <i class="fas fa-plus me-1"></i> <?= t('Add plant') ?>
</button>

<!-- ---------- OFFCANVAS FORM ------------------------------------------ -->
<div class="offcanvas offcanvas-end" tabindex="-1" id="plantFormCanvas">
  <div class="offcanvas-header">
    <h5 class="offcanvas-title"><?= $isEdit ? t('Edit / Add stage') : t('Add plant') ?></h5>
    <button type="button" class="btn-close text-reset" data-bs-dismiss="offcanvas"></button>
  </div>
  <div class="offcanvas-body">

    <form method="post" id="plant-form">
    <?php if ($isEdit): ?><input type="hidden" name="idx" value="<?= $idx ?>"><?php endif; ?>

    <h6 class="mt-2 mb-3 text-uppercase text-muted small"><?= t('Basic info') ?></h6>
    <div class="row g-3">
      <div class="col-sm-6">
        <label class="form-label"><?= t('Common name') ?></label>
        <input class="form-control" name="common" value="<?= e($form['common']) ?>" required>
      </div>
      <div class="col-sm-6">
        <label class="form-label"><?= t('Latin name') ?></label>
        <input class="form-control" name="latin" value="<?= e($form['latin']) ?>" required>
      </div>
    </div>

    <div class="row g-3 mt-2">
    <div class="col-sm-6">
      <label class="form-label">Location</label>
      <input class="form-control" name="location" list="locations" value="<?= e($form['location']) ?>">
      <datalist id="locations">
        <?php
        $seen = [];
        foreach ($plants as $p) {
            if (!empty($p['location']) && !in_array($p['location'], $seen, true)) {
                echo '<option value="' . e($p['location']) . '">';
                $seen[] = $p['location'];
            }
        }
        ?>
      </datalist>
    </div>
    <div class="col-sm-6">
      <label class="form-label">Notes</label>
      <input class="form-control" name="notes" value="<?= e($form['notes']) ?>">
    </div>
  </div>

  <h6 class="mt-4 mb-3 text-uppercase text-muted small"><?= t('Current stage') ?></h6>
<div class="row row-cols-2 g-3 text-center mb-2">
<?php
$stageButtons = [
    'strat'  => ['fa-snowflake', 'info',    t('Strat')],
    'soak'   => ['fa-tint',      'primary', t('Soak')],
    'sow'    => ['fa-seedling',  'success', t('Sow')],
    'sprout' => ['fa-leaf',      'success', t('Sprout')],
];
foreach ($stageButtons as $v => [$iconClass, $color, $label]): ?>
  <div class="col">
    <input type="radio" class="btn-check" name="status" id="btn-<?= $v ?>" value="<?= $v ?>" <?= $form['status'] === $v ? 'checked' : '' ?>>
    <label class="btn btn-outline-<?= $color ?> w-100" for="btn-<?= $v ?>">
      <i class="fas <?= $iconClass ?> me-1"></i> <?= $label ?>
    </label>
  </div>
<?php endforeach; ?>
</div>




    <!-- sow section -->
    <div id="section-sow" class="status-section">
      <div class="mb-3">
        <label class="form-label"><?= t('Sow date') ?></label>
        <input type="date" class="form-control" name="date_sow" value="<?= e($form['date_sow']) ?>">
      </div>
      <div class="row g-3">
        <div class="col-sm-6">
          <label class="form-label"><?= t('Est. sprout time (min)') ?></label>
          <div class="input-group">
            <input type="number" min="1" class="form-control" name="sprout_min" value="<?= e($form['sprout_min']) ?>">
            <select class="form-select" name="sprout_min_u">
              <?php foreach (['days', 'weeks', 'months'] as $u): ?>
                <option value="<?= $u ?>" <?= $form['sprout_min_u'] === $u ? 'selected' : '' ?>><?= t($u) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
        </div>
        <div class="col-sm-6">
          <label class="form-label"><?= t('Est. sprout time (max)') ?></label>
          <div class="input-group">
            <input type="number" min="1" class="form-control" name="sprout_max" value="<?= e($form['sprout_max']) ?>">
            <select class="form-select" name="sprout_max_u">
              <?php foreach (['days', 'weeks', 'months'] as $u): ?>
                <option value="<?= $u ?>" <?= $form['sprout_max_u'] === $u ? 'selected' : '' ?>><?= t($u) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- soak section -->
    <div id="section-soak" class="status-section">
      <div class="row g-3">
        <div class="col-sm-6">
          <label class="form-label"><?= t('Soak start date') ?></label>
          <input type="date" class="form-control" name="soak_date" value="<?= e($form['soak_date']) ?>">
        </div>
        <div class="col-sm-6">
          <label class="form-label"><?= t('Soak duration') ?></label>
          <div class="input-group">
            <input type="number" min="1" class="form-control" name="soak_val" value="<?= e($form['soak_val']) ?>">
            <select class="form-select" name="soak_unit">
              <?php foreach (['hours', 'days'] as $u): ?>
                <option value="<?= $u ?>" <?= $form['soak_unit'] === $u ? 'selected' : '' ?>><?= t($u) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- strat section -->
    <div id="section-strat" class="status-section">
      <div class="row g-3">
        <div class="col-sm-6">
          <label class="form-label"><?= t('Strat start date') ?></label>
          <input type="date" class="form-control" name="strat_date" value="<?= e($form['strat_date']) ?>">
        </div>
        <div class="col-sm-6">
          <label class="form-label"><?= t('Strat duration') ?></label>
          <div class="input-group">
            <input type="number" min="1" class="form-control" name="strat_val" value="<?= e($form['strat_val']) ?>">
            <select class="form-select" name="strat_unit">
              <?php foreach (['days', 'weeks', 'months'] as $u): ?>
                <option value="<?= $u ?>" <?= $form['strat_unit'] === $u ? 'selected' : '' ?>><?= t($u) ?></option>
              <?php endforeach; ?>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- sprout section -->
    <div id="section-sprout" class="status-section">
      <label class="form-label"><?= t('Sprouted on') ?></label>
      <input type="date" class="form-control" name="sprout_date" value="<?= e($form['sprout_date']) ?>">
    </div>

    <button class="btn btn-primary mt-4 w-100"><?= t('Save') ?></button>
    </form>
  </div>
</div>

<!-- ---------- PLANT LIST ---------------------------------------------- -->
<?php if ($plants): ?>
  <?php
    // Define stage order for sorting
    $stage_order = ['soak' => 0, 'strat' => 1, 'sow' => 2, 'sprout' => 3];

    usort($plants, function ($a, $b) use ($stage_order) {
        $a_stage = $a['history'][0]['action'];
        $b_stage = $b['history'][0]['action'];

        $a_stage_rank = $stage_order[$a_stage] ?? 99;
        $b_stage_rank = $stage_order[$b_stage] ?? 99;

        // First, sort by stage priority
        if ($a_stage_rank !== $b_stage_rank) {
            return $a_stage_rank <=> $b_stage_rank;
        }

        // Then sort by remaining days (ascending, i.e., closer deadlines first)
        $get_remaining = function ($entry) {
            $now = new DateTime();
            $act = $entry['history'][0];
            $action = $act['action'];

            if ($action === 'sow' && isset($act['range'])) {
                [$min, $minU] = [$act['range'][0], $act['range'][1]];
                $start = new DateTime($act['start']);
                $minDays = match ($minU) {
                    'weeks' => $min * 7,
                    'months' => $min * 30,
                    default => $min,
                };
                $due = (clone $start)->modify("+{$minDays} days");
                return max(0, $now->diff($due)->days);
            } elseif (in_array($action, ['soak', 'strat'])) {
                [$val, $unit] = $act['duration'];
                $days = match ($unit) {
                    'weeks' => $val * 7,
                    'months' => $val * 30,
                    'hours' => (int)round($val / 24),
                    default => $val,
                };
                $start = new DateTime($act['start']);
                $end = (clone $start)->modify("+{$days} days");
                return max(0, $now->diff($end)->days);
            } elseif ($action === 'sprout') {
                // Consider sprouted as "completed" – give them a large number to push to bottom
                return 9999;
            }
            return 9999;
        };

        return $get_remaining($a) <=> $get_remaining($b);
    });
    ?>
  <h2 class="h4 mb-3 mt-4"><?= t('My plants') ?></h2>
  <div class="accordion" id="plants-acc">
  <?php foreach ($plants as $i => $p):
        $curr = $p['history'][0];
        [$ic, $col] = $icon[$curr['action']];
        $summary = '';

        switch ($curr['action']) {
            case 'sow':
                $summary = t('Sow') . ' ' . $curr['start'];
                if (isset($curr['range'])) {
                    [$min, $minU, $max, $maxU] = $curr['range'];
                    $minDays = duration_to_days($min, $minU);
                    $maxDays = duration_to_days($max, $maxU);
                    $dStart = new DateTime($curr['start']);
                    $dMin   = (clone $dStart)->modify("+{$minDays} days");
                    $dMax   = (clone $dStart)->modify("+{$maxDays} days");
                    $now    = new DateTime();
                    if ($now > $dMax) {
                        $summary .= ' <span class="badge bg-danger ms-2">' . t('Overdue') . '</span>';
                    } elseif ($now >= $dMin && $now <= $dMax) {
                        $summary .= ' <span class="badge bg-warning text-dark ms-2">' . t('Anytime soon') . '</span>';
                    } else {
                        $remaining = $now->diff($dMin)->days;
                        $summary .= ' <span class="badge bg-info text-dark ms-2">' . $remaining . ' ' . t('days') . ' ' . t('left') . '</span>';
                    }
                }
                break;
            case 'soak':
            case 'strat':
                [$v, $u] = $curr['duration'];
                $days = duration_to_days($v, $u);
                $start = new DateTime($curr['start']);
                $end   = (clone $start)->modify("+{$days} days");
                $now   = new DateTime();
                $label = t(ucfirst($curr['action']));
                if ($now > $end) {
                    $summary = "$label <span class=\"badge bg-danger ms-2\">" . t('Overdue') . '</span>';
                } else {
                    $remaining = $now->diff($end)->days;
                    $summary = "$label <span class=\"badge bg-info text-dark ms-2\">$remaining " . t('days') . ' ' . t('left') . '</span>';
                }
                break;
            case 'sprout':
                $summary = t('Sprouted on') . ' ' . $curr['start'];
                $summary .= ' <span class="badge bg-success ms-2"><i class="fas fa-check"></i></span>';
                break;
        }
  ?>
    <div class="accordion-item">
      <h2 class="accordion-header" id="h<?= $i ?>">
        <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#c<?= $i ?>">
          <span class="me-3"><i class="fas <?= "$ic $col" ?>"></i></span>
          <div class="flex-grow-1">
            <div class="fw-semibold"><?= e($p['common']) ?> <small class="text-muted fst-italic">(<?= e($p['latin']) ?>)</small></div>
            <div class="small text-muted"><?= $summary ?></div>
            <?php if (!empty($p['location'])): ?>
              <div class="small text-muted"><i class="fas fa-location-dot me-1"></i><?= e($p['location']) ?></div>
            <?php endif; ?>
            <?php if (!empty($p['notes'])): ?>
              <div class="small text-muted"><i class="fas fa-sticky-note me-1"></i><?= e($p['notes']) ?></div>
            <?php endif; ?>
          </div>
        </button>
      </h2>
      <div id="c<?= $i ?>" class="accordion-collapse collapse" data-bs-parent="#plants-acc">
        <div class="accordion-body">
          <ul class="timeline">
            <?php foreach ($p['history'] as $st):
              [$icH, $colH] = $icon[$st['action']];
              switch ($st['action']) {
                case 'sow':
                    $txt = t('Sow') . ' ' . $st['start'];
                    if (isset($st['range']) && !array_filter($p['history'], fn($h) => $h['action'] === 'sprout' && $h !== $st)) {
                        [$min, $minU, $max, $maxU] = $st['range'];
                        $minDays = duration_to_days($min, $minU);
                        $maxDays = duration_to_days($max, $maxU);
                        $dStart = new DateTime($st['start']);
                        $dMin   = (clone $dStart)->modify("+{$minDays} days");
                        $dMax   = (clone $dStart)->modify("+{$maxDays} days");
                
                        $txt .= ' – ' . t('Sprout') . ' ' . sprintf(t('between_dates'), $dMin->format('Y-m-d'), $dMax->format('Y-m-d'));
                
                        if ($now > $dMax) {
                            $txt .= ' (' . t('finished') . ')';
                        } elseif ($now >= $dMin && $now <= $dMax) {
                            $txt .= ' (' . t('Anytime soon') . ')';
                        } else {
                            $txt .= ' (' . sprintf(t('remaining_range'), 
                                $now->diff($dMin)->days, 
                                $now->diff($dMax)->days
                            ) . ')';
                        }
                    }
                    break;                
                  case 'soak':
                  case 'strat':
                      [$v, $u] = $st['duration'];
                      $days = duration_to_days($v, $u);
                      $end = date('Y-m-d', strtotime($st['start'] . " +{$days} days"));
                      $txt = t(ucfirst($st['action'])) . " {$v} " . t($u) . ", " . t('ends') . " $end (" . remaining_until($end) . ')';
                      break;
                  case 'sprout':
                      $txt = t('Sprouted on') . ' ' . $st['start'];
                      break;
                      default:
                      $txt = ucfirst($st['action']) . ' ' . $st['start'];
              }
            ?>
            <li class="timeline-item">
              <div class="timeline-icon <?= $colH ?>"><i class="fas <?= $icH ?>"></i></div>
              <div><?= e($txt) ?></div>
            </li>
            <?php endforeach; ?>
          </ul>
          <a href="?edit=<?= $i ?>&lang=<?= $lang ?>" class="btn btn-sm btn-outline-secondary mt-3">
            <i class="fas fa-edit me-1"></i> <?= t('Edit / Add stage') ?>
          </a>
        </div>
      </div>
    </div>
  <?php endforeach; ?>
  </div>
<?php endif; ?>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', () => {
    function toggleSections() {
        document.querySelectorAll('.status-section').forEach(section => {
            const show = section.id === 'section-' + document.querySelector('input[name="status"]:checked')?.value;
            section.style.display = show ? 'block' : 'none';
            section.querySelectorAll('input, select').forEach(el => {
                el.disabled = !show;
            });
        });
    }

    toggleSections();
    document.querySelectorAll('input[name="status"]').forEach(el => {
        el.addEventListener('change', toggleSections);
    });

    <?php if ($isEdit): ?>
    const offcanvas = new bootstrap.Offcanvas('#plantFormCanvas');
    offcanvas.show();
    <?php endif; ?>
});
</script>

</body>
</html>
