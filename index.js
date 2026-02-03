const express = require('express');
const cors = require('cors');
const { google } = require('googleapis');

const app = express();
app.use(cors());
app.use(express.json());

// Initialize Google Sheets API
const getAuth = () => {
  const email = process.env.GOOGLE_SERVICE_ACCOUNT_EMAIL;
  let privateKey = process.env.GOOGLE_PRIVATE_KEY;

  if (privateKey) {
    privateKey = privateKey.replace(/\\n/g, '\n');
  }

  const auth = new google.auth.GoogleAuth({
    credentials: {
      client_email: email,
      private_key: privateKey,
    },
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return auth;
};

const getSheets = async () => {
  const auth = getAuth();
  return google.sheets({ version: 'v4', auth });
};

const SHEET_ID = process.env.GOOGLE_SHEET_ID || '1DZfS3J6Men-XuX11nXAYnMhgQKCn4SJTFDDFxlEQAwk';
const SHEET_NAME = 'BPV';
const ALL_BPV_SHEET = 'all Bpv';

// Salary/Employee sheets (for payroll system)
const SALARY_SHEET_ID = process.env.SALARY_SHEET_ID || '1WpLT7IK1x5Pm5Dzj-q98oMCYrzHnFbHMT0DDJN4m7k4';
const LABOUR_SALARY_SHEET = 'Labour salary';
const STAFF_SALARY_SHEET = 'Staff salary';

// Attendance sheet
const ATTENDANCE_SHEET_ID = process.env.ATTENDANCE_SHEET_ID || '1NjZxG_LctqXZP2nk1HvXbFG4rVVzeK6H4WZ-4iRtODE';
const LABOUR_ATTENDANCE_SHEET = 'Labour attendence';

// Build voucher position map
const buildVoucherPositionMap = () => {
  const positions = {};

  const oldFormatBases = [5, 28, 48, 68, 88, 108, 128, 148, 168];
  oldFormatBases.forEach((base, i) => {
    positions[i + 1] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'old' };
  });

  const transitional = [[10, 194], [11, 221], [12, 241], [13, 261], [14, 287], [15, 313], [16, 338], [17, 366]];
  transitional.forEach(([bpvNum, base]) => {
    positions[bpvNum] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'transitional' };
  });

  for (let i = 0; i < 43; i++) {
    const base = 393 + (i * 27);
    positions[18 + i] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'new' };
  }

  return positions;
};

const VOUCHER_POSITIONS = buildVoucherPositionMap();

const formatDate = (value) => {
  if (!value) return '';
  if (typeof value === 'number') {
    const date = new Date((value - 25569) * 86400 * 1000);
    return date.toLocaleDateString('en-GB');
  }
  return String(value);
};

const parseAmount = (value) => {
  if (!value) return 0;
  if (typeof value === 'number') return value;
  return parseFloat(String(value).replace(/,/g, '')) || 0;
};

const getCell = (rows, rowIdx, colIdx) => {
  if (rowIdx < rows.length && colIdx < rows[rowIdx].length) return rows[rowIdx][colIdx];
  return '';
};

const parseVoucherAtPosition = (rows, bpvNum, pos) => {
  const bpvNoRow = pos.bpvNoRow - 1;
  const dateRow = pos.dateRow - 1;
  const dataRow = pos.dataRow - 1;
  const totalRow = pos.totalRow - 1;

  const bpvNo = getCell(rows, bpvNoRow, 3);
  const date = getCell(rows, dateRow, 3);
  const pdcType = getCell(rows, bpvNoRow, 4) || 'PDC';

  const lineItems = [];
  const skipLabels = ['TOTAL AMOUNT', 'Prepared By', 'Received By', 'Approved By', 'Checked By', '___'];

  for (let i = 0; i < 5; i++) {
    const itemRow = dataRow + i;
    const description = getCell(rows, itemRow, 1);
    if (skipLabels.some(label => String(description).includes(label))) continue;

    const debit = getCell(rows, itemRow, 5);
    const credit = getCell(rows, itemRow, 6);
    const companyName = getCell(rows, itemRow, 2);

    if ((Boolean(companyName) || Boolean(description)) && (parseAmount(debit) > 0 || parseAmount(credit) > 0)) {
      lineItems.push({
        srNo: getCell(rows, itemRow, 0) || String(lineItems.length + 1),
        description, companyName,
        chequeNo: getCell(rows, itemRow, 3),
        chequeDate: formatDate(getCell(rows, itemRow, 4)),
        debit: parseAmount(debit),
        credit: parseAmount(credit)
      });
    }
  }

  const totalAmount = parseAmount(getCell(rows, totalRow, 2));
  return {
    id: bpvNum, bpvNo, date: formatDate(date), pdcType, lineItems, totalAmount,
    hasData: lineItems.length > 0 || totalAmount > 0, baseRow: pos.dataRow, format: pos.format
  };
};

// API Routes
app.get('/api/bpv', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A1:G2000`,
    });

    const rows = response.data.values || [];
    const vouchers = [];

    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      try {
        const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
        if (voucher && voucher.hasData) vouchers.push(voucher);
      } catch (error) {
        console.error(`Error parsing BPV #${bpvNumStr}:`, error);
      }
    }

    res.json({ success: true, data: vouchers });
  } catch (error) {
    console.error('Error:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

app.get('/api/bpv/next-number', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A1:G2000`,
    });

    const rows = response.data.values || [];
    const vouchers = [];

    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      try {
        const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
        if (voucher && voucher.hasData) vouchers.push(voucher);
      } catch (error) {}
    }

    const bpvNumbers = vouchers.map(v => parseInt(v.bpvNo)).filter(n => !isNaN(n));
    const nextNumber = bpvNumbers.length > 0 ? Math.max(...bpvNumbers) + 1 : 1;

    res.json({ success: true, data: { nextNumber } });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

app.get('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];

    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();
    const startRow = pos.bpvNoRow - 5;
    const endRow = pos.totalRow + 10;

    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A${startRow}:G${endRow}`,
    });

    const rows = response.data.values || [];
    const adjustedPos = {
      bpvNoRow: pos.bpvNoRow - startRow + 1,
      dateRow: pos.dateRow - startRow + 1,
      dataRow: pos.dataRow - startRow + 1,
      totalRow: pos.totalRow - startRow + 1,
      format: pos.format
    };

    const voucher = parseVoucherAtPosition(rows, bpvNum, adjustedPos);
    res.json({ success: true, data: voucher });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

const calculateTotal = (lineItems) => {
  return Math.round((lineItems || []).reduce((sum, item) => sum + (parseAmount(item.debit) || 0), 0) * 100) / 100;
};

const syncToAllBpv = async (bpvNum, voucherData) => {
  const sheets = await getSheets();
  const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:A` });
  const rows = response.data.values || [];

  let targetRow = -1;
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] && parseInt(rows[i][0]) === bpvNum) { targetRow = i + 1; break; }
  }

  const firstItem = voucherData.lineItems?.[0] || {};
  const rowData = [[
    voucherData.bpvNo || bpvNum, firstItem.companyName || '', firstItem.description || '',
    voucherData.date || '', firstItem.chequeNo || '', firstItem.chequeDate || '',
    voucherData.totalAmount || '', voucherData.pdcType || 'PDC'
  ]];

  if (targetRow > 0) {
    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A${targetRow}:H${targetRow}`,
      valueInputOption: 'USER_ENTERED', requestBody: { values: rowData }
    });
  } else {
    await sheets.spreadsheets.values.append({
      spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:H`,
      valueInputOption: 'USER_ENTERED', insertDataOption: 'INSERT_ROWS', requestBody: { values: rowData }
    });
  }
};

app.post('/api/bpv', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A1:G2000` });
    const rows = response.data.values || [];

    // Find empty slot
    const usedSlots = new Set();
    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
      if (voucher && voucher.hasData) usedSlots.add(parseInt(bpvNumStr));
    }

    let emptySlot = null;
    for (const bpvNum of Object.keys(VOUCHER_POSITIONS).map(k => parseInt(k)).sort((a, b) => a - b)) {
      if (!usedSlots.has(bpvNum)) { emptySlot = bpvNum; break; }
    }

    if (!emptySlot) return res.status(400).json({ success: false, error: 'No empty slots available' });

    // Update the slot
    const pos = VOUCHER_POSITIONS[emptySlot];
    const data = req.body;
    const requests = [];

    if (data.bpvNo !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.bpvNoRow}`, values: [[data.bpvNo]] });
    if (data.date !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.dateRow}`, values: [[data.date]] });
    if (data.pdcType !== undefined) requests.push({ range: `'${SHEET_NAME}'!E${pos.bpvNoRow}`, values: [[data.pdcType]] });

    if (data.lineItems) {
      const lineItems = data.lineItems.slice(0, 5);
      for (let i = 0; i < lineItems.length; i++) {
        const item = lineItems[i];
        requests.push({
          range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
          values: [[item.srNo || String(i + 1), item.description || '', item.companyName || '', item.chequeNo || '', item.chequeDate || '', item.debit || '', item.credit || '']]
        });
      }
      for (let i = lineItems.length; i < 5; i++) {
        requests.push({ range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`, values: [['', '', '', '', '', '', '']] });
      }
    }

    const total = data.totalAmount !== undefined ? data.totalAmount : calculateTotal(data.lineItems);
    requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[total]] });

    if (requests.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: SHEET_ID, requestBody: { data: requests, valueInputOption: 'USER_ENTERED' }
      });
    }

    // Get updated voucher and sync
    const getResponse = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${pos.bpvNoRow - 5}:G${pos.totalRow + 10}` });
    const adjustedPos = { bpvNoRow: 6, dateRow: 7, dataRow: 13, totalRow: 15, format: pos.format };
    const voucher = parseVoucherAtPosition(getResponse.data.values || [], emptySlot, adjustedPos);

    await syncToAllBpv(emptySlot, voucher);

    res.status(201).json({ success: true, data: voucher });
  } catch (error) {
    console.error('Create error:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

app.put('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];
    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();
    const data = req.body;
    const requests = [];

    if (data.bpvNo !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.bpvNoRow}`, values: [[data.bpvNo]] });
    if (data.date !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.dateRow}`, values: [[data.date]] });
    if (data.pdcType !== undefined) requests.push({ range: `'${SHEET_NAME}'!E${pos.bpvNoRow}`, values: [[data.pdcType]] });

    if (data.lineItems) {
      const lineItems = data.lineItems.slice(0, 5);
      for (let i = 0; i < lineItems.length; i++) {
        const item = lineItems[i];
        requests.push({
          range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
          values: [[item.srNo || String(i + 1), item.description || '', item.companyName || '', item.chequeNo || '', item.chequeDate || '', item.debit || '', item.credit || '']]
        });
      }
      for (let i = lineItems.length; i < 5; i++) {
        requests.push({ range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`, values: [['', '', '', '', '', '', '']] });
      }
    }

    if (data.totalAmount !== undefined) {
      requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[data.totalAmount]] });
    } else if (data.lineItems) {
      requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[calculateTotal(data.lineItems)]] });
    }

    if (requests.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: SHEET_ID, requestBody: { data: requests, valueInputOption: 'USER_ENTERED' }
      });
    }

    // Get updated voucher
    const startRow = pos.bpvNoRow - 5;
    const getResponse = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${startRow}:G${pos.totalRow + 10}` });
    const adjustedPos = {
      bpvNoRow: pos.bpvNoRow - startRow + 1, dateRow: pos.dateRow - startRow + 1,
      dataRow: pos.dataRow - startRow + 1, totalRow: pos.totalRow - startRow + 1, format: pos.format
    };
    const voucher = parseVoucherAtPosition(getResponse.data.values || [], bpvNum, adjustedPos);

    await syncToAllBpv(bpvNum, voucher);

    res.json({ success: true, data: voucher });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

app.delete('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];
    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!D${pos.bpvNoRow}:E${pos.bpvNoRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['', '']] }
    });

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!D${pos.dateRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['']] }
    });

    for (let i = 0; i < 5; i++) {
      await sheets.spreadsheets.values.update({
        spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
        valueInputOption: 'RAW', requestBody: { values: [[String(i + 1), '', '', '', '', '', '']] }
      });
    }

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!C${pos.totalRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['']] }
    });

    // Clear from all Bpv sheet
    const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:A` });
    const rows = response.data.values || [];
    for (let i = 0; i < rows.length; i++) {
      if (rows[i][0] && parseInt(rows[i][0]) === bpvNum) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A${i + 1}:H${i + 1}`,
          valueInputOption: 'RAW', requestBody: { values: [['', '', '', '', '', '', '', '']] }
        });
        break;
      }
    }

    res.json({ success: true, message: `Voucher #${bpvNum} deleted` });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// EMPLOYEE API ENDPOINTS (for Salary/Payroll)
// ============================================

// Get all employees (Labour + Staff)
app.get('/api/employees', async (req, res) => {
  try {
    const sheets = await getSheets();
    const type = req.query.type; // 'labour', 'staff', or undefined for all

    const employees = [];

    // Fetch Labour employees
    if (!type || type === 'labour') {
      const labourResponse = await sheets.spreadsheets.values.get({
        spreadsheetId: SALARY_SHEET_ID,
        range: `'${LABOUR_SALARY_SHEET}'!A:H`,
      });
      const labourRows = labourResponse.data.values || [];

      // Skip header row, parse employees
      for (let i = 1; i < labourRows.length; i++) {
        const row = labourRows[i];
        if (row[0] && row[1]) { // Must have Employee ID and Name
          employees.push({
            id: row[0],
            employeeId: row[0],
            name: row[1],
            designation: row[2] || '',
            ratePerDay: parseFloat(row[3]) || 0,
            ratePerHour: parseFloat(row[4]) || 0,
            otHours: parseFloat(row[5]) || 0,
            netSalary: parseFloat(row[6]) || 0,
            type: row[7] || 'Labour',
            rowIndex: i + 1, // 1-indexed for Google Sheets
            sheetType: 'labour'
          });
        }
      }
    }

    // Fetch Staff employees
    if (!type || type === 'staff') {
      const staffResponse = await sheets.spreadsheets.values.get({
        spreadsheetId: SALARY_SHEET_ID,
        range: `'${STAFF_SALARY_SHEET}'!A:G`,
      });
      const staffRows = staffResponse.data.values || [];

      for (let i = 1; i < staffRows.length; i++) {
        const row = staffRows[i];
        if (row[0] && row[1]) {
          employees.push({
            id: row[0],
            employeeId: row[0],
            name: row[1],
            ratePerDay: parseFloat(row[2]) || 0,
            designation: row[3] || '',
            deductions: parseFloat(row[4]) || 0,
            netSalary: parseFloat(row[5]) || 0,
            type: row[6] || 'Staff',
            rowIndex: i + 1,
            sheetType: 'staff'
          });
        }
      }
    }

    res.json({ success: true, data: employees });
  } catch (error) {
    console.error('Error fetching employees:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Get single employee
app.get('/api/employees/:id', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const sheets = await getSheets();

    // Search in Labour sheet
    const labourResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:H`,
    });
    const labourRows = labourResponse.data.values || [];

    for (let i = 1; i < labourRows.length; i++) {
      const row = labourRows[i];
      if (row[0] === employeeId) {
        return res.json({
          success: true,
          data: {
            id: row[0],
            employeeId: row[0],
            name: row[1],
            designation: row[2] || '',
            ratePerDay: parseFloat(row[3]) || 0,
            ratePerHour: parseFloat(row[4]) || 0,
            otHours: parseFloat(row[5]) || 0,
            netSalary: parseFloat(row[6]) || 0,
            type: row[7] || 'Labour',
            rowIndex: i + 1,
            sheetType: 'labour'
          }
        });
      }
    }

    // Search in Staff sheet
    const staffResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${STAFF_SALARY_SHEET}'!A:G`,
    });
    const staffRows = staffResponse.data.values || [];

    for (let i = 1; i < staffRows.length; i++) {
      const row = staffRows[i];
      if (row[0] === employeeId) {
        return res.json({
          success: true,
          data: {
            id: row[0],
            employeeId: row[0],
            name: row[1],
            ratePerDay: parseFloat(row[2]) || 0,
            designation: row[3] || '',
            deductions: parseFloat(row[4]) || 0,
            netSalary: parseFloat(row[5]) || 0,
            type: row[6] || 'Staff',
            rowIndex: i + 1,
            sheetType: 'staff'
          }
        });
      }
    }

    res.status(404).json({ success: false, error: 'Employee not found' });
  } catch (error) {
    console.error('Error fetching employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Get next available Employee ID
app.get('/api/employees/next-id', async (req, res) => {
  try {
    const type = req.query.type || 'labour';
    const sheets = await getSheets();

    const sheetName = type === 'staff' ? STAFF_SALARY_SHEET : LABOUR_SALARY_SHEET;
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${sheetName}'!A:A`,
    });

    const rows = response.data.values || [];
    const ids = rows.slice(1).map(r => parseInt(r[0])).filter(n => !isNaN(n));
    const nextId = ids.length > 0 ? Math.max(...ids) + 1 : 1;

    res.json({ success: true, data: { nextId } });
  } catch (error) {
    console.error('Error getting next ID:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Create new employee
app.post('/api/employees', async (req, res) => {
  try {
    const sheets = await getSheets();
    const data = req.body;
    const isStaff = data.type === 'Staff';
    const sheetName = isStaff ? STAFF_SALARY_SHEET : LABOUR_SALARY_SHEET;

    // Get next ID if not provided
    let employeeId = data.employeeId;
    if (!employeeId) {
      const response = await sheets.spreadsheets.values.get({
        spreadsheetId: SALARY_SHEET_ID,
        range: `'${sheetName}'!A:A`,
      });
      const rows = response.data.values || [];
      const ids = rows.slice(1).map(r => parseInt(r[0])).filter(n => !isNaN(n));
      employeeId = ids.length > 0 ? Math.max(...ids) + 1 : 1;
    }

    // Prepare row data based on sheet type
    let rowData;
    if (isStaff) {
      // Staff: Employee ID, Name, Rate per day, Designation, Deductions, Net salary, Type
      rowData = [[
        String(employeeId),
        data.name || '',
        data.ratePerDay || '',
        data.designation || '',
        data.deductions || '',
        data.netSalary || '',
        'Staff'
      ]];
    } else {
      // Labour: Employee ID, Name, Designation, Rate per day, Rate per hour, OT hours, Net salary, Type
      rowData = [[
        String(employeeId),
        data.name || '',
        data.designation || '',
        data.ratePerDay || '',
        data.ratePerHour || (data.ratePerDay ? (parseFloat(data.ratePerDay) / 8).toFixed(2) : ''),
        data.otHours || '',
        data.netSalary || '',
        'Labour'
      ]];
    }

    await sheets.spreadsheets.values.append({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${sheetName}'!A:H`,
      valueInputOption: 'USER_ENTERED',
      insertDataOption: 'INSERT_ROWS',
      requestBody: { values: rowData }
    });

    res.status(201).json({
      success: true,
      data: {
        id: String(employeeId),
        employeeId: String(employeeId),
        name: data.name,
        designation: data.designation,
        ratePerDay: parseFloat(data.ratePerDay) || 0,
        ratePerHour: parseFloat(data.ratePerHour) || 0,
        otHours: parseFloat(data.otHours) || 0,
        netSalary: parseFloat(data.netSalary) || 0,
        type: isStaff ? 'Staff' : 'Labour',
        sheetType: isStaff ? 'staff' : 'labour'
      }
    });
  } catch (error) {
    console.error('Error creating employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Update employee
app.put('/api/employees/:id', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const sheets = await getSheets();
    const data = req.body;

    // Find employee in Labour sheet
    const labourResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:H`,
    });
    const labourRows = labourResponse.data.values || [];

    for (let i = 1; i < labourRows.length; i++) {
      if (labourRows[i][0] === employeeId) {
        const rowData = [[
          employeeId,
          data.name || labourRows[i][1] || '',
          data.designation || labourRows[i][2] || '',
          data.ratePerDay !== undefined ? data.ratePerDay : labourRows[i][3] || '',
          data.ratePerHour !== undefined ? data.ratePerHour : labourRows[i][4] || '',
          data.otHours !== undefined ? data.otHours : labourRows[i][5] || '',
          data.netSalary !== undefined ? data.netSalary : labourRows[i][6] || '',
          labourRows[i][7] || 'Labour'
        ]];

        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${LABOUR_SALARY_SHEET}'!A${i + 1}:H${i + 1}`,
          valueInputOption: 'USER_ENTERED',
          requestBody: { values: rowData }
        });

        return res.json({ success: true, data: { ...data, id: employeeId, type: 'Labour' } });
      }
    }

    // Find in Staff sheet
    const staffResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${STAFF_SALARY_SHEET}'!A:G`,
    });
    const staffRows = staffResponse.data.values || [];

    for (let i = 1; i < staffRows.length; i++) {
      if (staffRows[i][0] === employeeId) {
        const rowData = [[
          employeeId,
          data.name || staffRows[i][1] || '',
          data.ratePerDay !== undefined ? data.ratePerDay : staffRows[i][2] || '',
          data.designation || staffRows[i][3] || '',
          data.deductions !== undefined ? data.deductions : staffRows[i][4] || '',
          data.netSalary !== undefined ? data.netSalary : staffRows[i][5] || '',
          staffRows[i][6] || 'Staff'
        ]];

        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${STAFF_SALARY_SHEET}'!A${i + 1}:G${i + 1}`,
          valueInputOption: 'USER_ENTERED',
          requestBody: { values: rowData }
        });

        return res.json({ success: true, data: { ...data, id: employeeId, type: 'Staff' } });
      }
    }

    res.status(404).json({ success: false, error: 'Employee not found' });
  } catch (error) {
    console.error('Error updating employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Delete employee
app.delete('/api/employees/:id', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const sheets = await getSheets();

    // Search in Labour sheet
    const labourResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:A`,
    });
    const labourRows = labourResponse.data.values || [];

    for (let i = 1; i < labourRows.length; i++) {
      if (labourRows[i][0] === employeeId) {
        // Clear the row
        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${LABOUR_SALARY_SHEET}'!A${i + 1}:H${i + 1}`,
          valueInputOption: 'RAW',
          requestBody: { values: [['', '', '', '', '', '', '', '']] }
        });
        return res.json({ success: true, message: `Employee ${employeeId} deleted from Labour` });
      }
    }

    // Search in Staff sheet
    const staffResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${STAFF_SALARY_SHEET}'!A:A`,
    });
    const staffRows = staffResponse.data.values || [];

    for (let i = 1; i < staffRows.length; i++) {
      if (staffRows[i][0] === employeeId) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${STAFF_SALARY_SHEET}'!A${i + 1}:G${i + 1}`,
          valueInputOption: 'RAW',
          requestBody: { values: [['', '', '', '', '', '', '']] }
        });
        return res.json({ success: true, message: `Employee ${employeeId} deleted from Staff` });
      }
    }

    res.status(404).json({ success: false, error: 'Employee not found' });
  } catch (error) {
    console.error('Error deleting employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// ATTENDANCE API ENDPOINTS
// ============================================

// Get attendance for a specific month
app.get('/api/attendance', async (req, res) => {
  try {
    const sheets = await getSheets();

    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${LABOUR_ATTENDANCE_SHEET}'!A:AF`, // Columns A to AF (31 days + employee info)
    });

    const rows = response.data.values || [];
    if (rows.length === 0) {
      return res.json({ success: true, data: [] });
    }

    const headers = rows[0];
    const attendance = [];

    for (let i = 1; i < rows.length; i++) {
      const row = rows[i];
      if (!row[0]) continue; // Skip empty rows

      const record = {
        employeeId: row[0],
        name: row[1],
        designation: row[2],
        rowIndex: i + 1,
        days: {}
      };

      // Parse days 1-31 (columns D onwards, index 3+)
      for (let day = 1; day <= 31; day++) {
        const colIndex = day + 2; // Day 1 is at index 3
        if (colIndex < row.length) {
          record.days[day] = row[colIndex] || '';
        }
      }

      attendance.push(record);
    }

    res.json({ success: true, data: attendance });
  } catch (error) {
    console.error('Error fetching attendance:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Update attendance for a specific employee and day
app.put('/api/attendance/:employeeId/:day', async (req, res) => {
  try {
    const { employeeId, day } = req.params;
    const { status } = req.body;
    const dayNum = parseInt(day);

    if (dayNum < 1 || dayNum > 31) {
      return res.status(400).json({ success: false, error: 'Invalid day' });
    }

    const sheets = await getSheets();

    // Find the employee row
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${LABOUR_ATTENDANCE_SHEET}'!A:A`,
    });
    const rows = response.data.values || [];

    let targetRow = -1;
    for (let i = 1; i < rows.length; i++) {
      if (rows[i][0] === employeeId) {
        targetRow = i + 1;
        break;
      }
    }

    if (targetRow === -1) {
      return res.status(404).json({ success: false, error: 'Employee not found in attendance sheet' });
    }

    // Column for the day (Day 1 = Column D = index 4 in A1 notation)
    const colLetter = String.fromCharCode(67 + dayNum); // C=67, so day 1 = D

    await sheets.spreadsheets.values.update({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${LABOUR_ATTENDANCE_SHEET}'!${colLetter}${targetRow}`,
      valueInputOption: 'USER_ENTERED',
      requestBody: { values: [[status]] }
    });

    res.json({ success: true, message: `Updated ${employeeId} day ${day} to ${status}` });
  } catch (error) {
    console.error('Error updating attendance:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Bulk update attendance for multiple employees on a day
app.post('/api/attendance/bulk', async (req, res) => {
  try {
    const { day, updates } = req.body; // updates: [{ employeeId, status }, ...]
    const dayNum = parseInt(day);

    if (dayNum < 1 || dayNum > 31) {
      return res.status(400).json({ success: false, error: 'Invalid day' });
    }

    const sheets = await getSheets();

    // Get all employee IDs and their row numbers
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${LABOUR_ATTENDANCE_SHEET}'!A:A`,
    });
    const rows = response.data.values || [];

    const employeeRowMap = {};
    for (let i = 1; i < rows.length; i++) {
      if (rows[i][0]) {
        employeeRowMap[rows[i][0]] = i + 1;
      }
    }

    // Column for the day
    const colLetter = String.fromCharCode(67 + dayNum);

    // Prepare batch update
    const batchData = [];
    for (const update of updates) {
      const rowNum = employeeRowMap[update.employeeId];
      if (rowNum) {
        batchData.push({
          range: `'${LABOUR_ATTENDANCE_SHEET}'!${colLetter}${rowNum}`,
          values: [[update.status]]
        });
      }
    }

    if (batchData.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: ATTENDANCE_SHEET_ID,
        requestBody: {
          data: batchData,
          valueInputOption: 'USER_ENTERED'
        }
      });
    }

    res.json({ success: true, message: `Updated ${batchData.length} attendance records` });
  } catch (error) {
    console.error('Error bulk updating attendance:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// OT/DEDUCTIONS API ENDPOINTS
// ============================================

// Update OT hours for an employee (in Salary sheet)
app.put('/api/employees/:id/ot', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const { otHours } = req.body;
    const sheets = await getSheets();

    // Find in Labour sheet (OT hours is column F, index 5)
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:A`,
    });
    const rows = response.data.values || [];

    for (let i = 1; i < rows.length; i++) {
      if (rows[i][0] === employeeId) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${LABOUR_SALARY_SHEET}'!F${i + 1}`,
          valueInputOption: 'USER_ENTERED',
          requestBody: { values: [[otHours]] }
        });
        return res.json({ success: true, message: `Updated OT hours for ${employeeId}` });
      }
    }

    res.status(404).json({ success: false, error: 'Employee not found' });
  } catch (error) {
    console.error('Error updating OT:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Bulk update OT hours for multiple employees
app.post('/api/employees/ot/bulk', async (req, res) => {
  try {
    const { updates } = req.body; // [{ employeeId, otHours }, ...]
    const sheets = await getSheets();

    // Get all employee IDs and their row numbers
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:A`,
    });
    const rows = response.data.values || [];

    const employeeRowMap = {};
    for (let i = 1; i < rows.length; i++) {
      if (rows[i][0]) {
        employeeRowMap[rows[i][0]] = i + 1;
      }
    }

    // Prepare batch update
    const batchData = [];
    for (const update of updates) {
      const rowNum = employeeRowMap[update.employeeId];
      if (rowNum) {
        batchData.push({
          range: `'${LABOUR_SALARY_SHEET}'!F${rowNum}`,
          values: [[update.otHours]]
        });
      }
    }

    if (batchData.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: SALARY_SHEET_ID,
        requestBody: {
          data: batchData,
          valueInputOption: 'USER_ENTERED'
        }
      });
    }

    res.json({ success: true, message: `Updated ${batchData.length} OT records` });
  } catch (error) {
    console.error('Error bulk updating OT:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Health check
app.get('/health', (req, res) => res.json({ status: 'ok' }));

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`BPV Backend running on port ${PORT}`));
